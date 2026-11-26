// After the launch exchange: go on to the interface from this origin, so the
// SameSite=Strict session cookie goes with the request, and leave no history
// entry for the exchange.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  window.location.replace(document.getElementById("continue").getAttribute("href"));
});
