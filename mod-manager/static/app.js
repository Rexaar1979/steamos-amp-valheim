// Ask before destructive clicks: <button data-confirm="Are you sure?">
document.addEventListener("click", function (e) {
  var btn = e.target.closest("[data-confirm]");
  if (btn && !window.confirm(btn.getAttribute("data-confirm"))) {
    e.preventDefault();
  }
});
