/* Keep browser tab title pinned to "AuraStudy" on landing + app shells. */
(function () {
  "use strict";

  var TITLE = "AuraStudy";

  function enforceTitle() {
    if (document.title !== TITLE) {
      document.title = TITLE;
    }
  }

  enforceTitle();

  if (typeof MutationObserver !== "undefined") {
    var titleEl = document.querySelector("title");
    if (titleEl) {
      new MutationObserver(enforceTitle).observe(titleEl, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
  }

  window.addEventListener("pageshow", enforceTitle);
  document.addEventListener("visibilitychange", enforceTitle);
  window.setInterval(enforceTitle, 2000);
})();
