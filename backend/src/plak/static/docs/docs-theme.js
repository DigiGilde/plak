// Sets the class swagger-ui.css hangs its dark theme on. That file does carry
// a complete dark theme (over 260 rules behind `html.dark-mode`) but not a
// single prefers-color-scheme query, so without this class the page stays
// light, even on a system set to dark.
//
// A file of its own and not an inline script, because script-src stays 'self'.
// In the <head> and without defer: run this after rendering instead and you
// get a flash of light first.

(function () {
  var donker = window.matchMedia('(prefers-color-scheme: dark)');

  function pasToe() {
    document.documentElement.classList.toggle('dark-mode', donker.matches);
  }

  pasToe();
  donker.addEventListener('change', pasToe);
})();
