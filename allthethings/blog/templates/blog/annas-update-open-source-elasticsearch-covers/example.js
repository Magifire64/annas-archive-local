var lastAnimationFrame = undefined;
var topByElement = {};

function render() {
  window.cancelAnimationFrame(lastAnimationFrame);
  lastAnimationFrame = window.requestAnimationFrame(() => {
    var bottomEdge = window.scrollY + window.innerHeight * 3; // Load 3 pages worth
    for (element of document.querySelectorAll(".js-scroll-hidden")) {
      if (!topByElement[element.id]) {
        topByElement[element.id] =
          element.getBoundingClientRect().top + window.scrollY;
      }
      if (topByElement[element.id] <= bottomEdge) {
        element.classList.remove("js-scroll-hidden");
        element.innerHTML = element.innerHTML
          .replace("<" + "!--", "")
          .replace("-" + "->", "");
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("scroll", () => {
    render();
  });
  render();
});
