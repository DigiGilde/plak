/**
 * Mini forward proxy for the browser during the E2E run.
 *
 * Why: the app advertises its authorize URL on the internal issuer origin
 * http://mock-oidc:8080, which exists only inside the compose network.
 * Playwright's browser routes do not intercept server redirects, and the
 * Node-side cookie jar does not keep Secure cookies over http, so the whole
 * login flow has to stay browser-native. With this proxy the browser resolves
 * nothing itself: mock-oidc:8080 is sent to the published mock port and
 * *.localhost to 127.0.0.1. Playwright tunnels proxied requests over CONNECT
 * (over http too), so that method is handled here as well; the whole stack is
 * local and unencrypted, so the tunnel is a bare TCP pass-through.
 */

import http from 'node:http';
import net from 'node:net';
import { MOCK_OIDC_ALIAS_HOSTNAME, MOCK_OIDC_INTERNAL, OIDC_PORT, PORT } from './environment';

const MOCK_HOST = new URL(MOCK_OIDC_INTERNAL).host;
const MOCK_HOSTNAME = new URL(MOCK_OIDC_INTERNAL).hostname;

/**
 * Local target port for a hostname: the internal mock origin to the published
 * mock port, the oidc alias (the issuer via the e2e nginx) to the e2e nginx
 * port regardless of the port in the URL, everything else to the given port.
 */
function targetPortFor(hostname: string, urlPort: number): number {
  if (hostname === MOCK_HOSTNAME) return Number(OIDC_PORT);
  if (hostname === MOCK_OIDC_ALIAS_HOSTNAME) return Number(PORT);
  return urlPort;
}

function isLocalHost(hostname: string): boolean {
  return (
    hostname === '127.0.0.1' ||
    hostname === 'localhost' ||
    hostname.endsWith('.localhost') ||
    hostname === MOCK_HOSTNAME
  );
}

export function startProxy(port: number): Promise<http.Server> {
  const server = http.createServer((request, response) => {
    let url: URL;
    try {
      // Forward proxy requests carry an absolute URL.
      url = new URL(request.url ?? '');
    } catch {
      response.writeHead(400).end('ongeldig proxyverzoek');
      return;
    }

    if (url.host !== MOCK_HOST && !isLocalHost(url.hostname)) {
      // The suite must touch nothing outside the local stack.
      response.writeHead(502).end(`proxy refuses non-local host ${url.host}`);
      return;
    }
    const targetPort = targetPortFor(url.hostname, url.port !== '' ? Number(url.port) : 80);

    const headers = { ...request.headers };
    delete headers['proxy-connection'];
    headers.host = url.host;

    const forwarded = http.request(
      {
        host: '127.0.0.1',
        port: targetPort,
        method: request.method,
        path: `${url.pathname}${url.search}`,
        headers,
      },
      (upstream) => {
        response.writeHead(upstream.statusCode ?? 502, upstream.headers);
        upstream.pipe(response);
      },
    );
    forwarded.on('error', (error) => {
      response.writeHead(502).end(`proxyfout: ${error.message}`);
    });
    request.pipe(forwarded);
  });

  // Playwright tunnels proxied requests over CONNECT. req.url is then
  // "host:port"; we open a bare TCP tunnel to the matching local port. The
  // browser then sends the real Host header through the tunnel, so nginx
  // routes on the right origin (admin/content).
  server.on('connect', (request, socket, head) => {
    const [hostname, portPart] = (request.url ?? '').split(':');
    if (!hostname || !isLocalHost(hostname)) {
      socket.end('HTTP/1.1 502 Bad Gateway\r\n\r\n');
      return;
    }
    const targetPort = targetPortFor(hostname, Number(portPart || 80));
    const upstream = net.connect(targetPort, '127.0.0.1', () => {
      socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head && head.length) upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    });
    upstream.on('error', () => socket.destroy());
    socket.on('error', () => upstream.destroy());
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}
