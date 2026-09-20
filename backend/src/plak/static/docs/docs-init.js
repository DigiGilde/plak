// Starts Swagger UI against our own OpenAPI schema. Deliberately a separate
// file and not an inline <script>: the beheer CSP allows script-src 'self' only.

(function () {
  var CSRF_COOKIE = '__Host-plak-csrf';
  var CSRF_HEADER = 'X-CSRF-Token';

  function csrfToken() {
    return document.cookie
      .split('; ')
      .filter(function (deel) {
        return deel.indexOf(CSRF_COOKIE + '=') === 0;
      })
      .map(function (deel) {
        return decodeURIComponent(deel.slice(CSRF_COOKIE.length + 1));
      })[0];
  }

  window.addEventListener('load', function () {
    window.ui = SwaggerUIBundle({
      url: '/-/api/openapi.json',
      dom_id: '#swagger-ui',
      presets: [SwaggerUIBundle.presets.apis],
      layout: 'BaseLayout',
      // The online validator at swagger.io would ship the whole schema off
      // site; this API is not public.
      validatorUrl: null,
      deepLinking: true,
      displayRequestDuration: true,
      docExpansion: 'list',
      filter: true,
      tryItOutEnabled: true,
      persistAuthorization: true,
      defaultModelsExpandDepth: 2,
      defaultModelExpandDepth: 4,
      defaultModelRendering: 'example',
      requestSnippetsEnabled: true,
      showExtensions: true,
      showCommonExtensions: true,
      syntaxHighlight: { activated: true, theme: 'idea' },
      requestInterceptor: function (verzoek) {
        // "Try it out" runs on the same origin as the SPA: the beheer session
        // has to ride along, and every mutation demands the double submit header.
        verzoek.credentials = 'same-origin';
        var token = csrfToken();
        if (token && ['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(verzoek.method) !== -1) {
          verzoek.headers[CSRF_HEADER] = token;
        }
        return verzoek;
      },
    });
  });
})();
