// Adds body.ui-idle after a few seconds of no interaction so secondary
// chrome (nav links, toggles, delete icons...) can recede via CSS and let
// the content itself hold the floor. Resets on any real interaction.
(() => {
  const IDLE_DELAY = 3000;
  let idleTimer = null;

  function markActive() {
    document.body.classList.remove('ui-idle');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => document.body.classList.add('ui-idle'), IDLE_DELAY);
  }

  ['mousemove', 'touchstart', 'click', 'keydown', 'scroll'].forEach(evt =>
    window.addEventListener(evt, markActive, { passive: true })
  );

  markActive();
})();
