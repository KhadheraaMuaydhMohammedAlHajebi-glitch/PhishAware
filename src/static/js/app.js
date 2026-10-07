// PhishAware client script (loaded from 'self' only, as the CSP requires).
// Prevents accidental double submission of forms marked data-once. Buttons are
// not disabled, because disabling the clicked button would drop its
// name/value pair (the participant's answer) from the submitted form.
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (!form.matches("form[data-once]")) {
    return;
  }
  if (form.dataset.submitted === "1") {
    event.preventDefault();
    return;
  }
  form.dataset.submitted = "1";
  form.classList.add("is-submitting");
});
