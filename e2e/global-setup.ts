import type http from 'node:http';
import { PROXY_PORT } from './helpers/environment';
import { startProxy } from './helpers/proxy';

let server: http.Server;

export default async function globalSetup(): Promise<() => Promise<void>> {
  server = await startProxy(Number(PROXY_PORT));
  return async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  };
}
