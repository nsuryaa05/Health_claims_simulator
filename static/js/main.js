// Small helpers. No framework needed.
document.addEventListener("DOMContentLoaded", function () {

  // 1. Confirmation dialogs: <form data-confirm="Are you sure?">
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) { event.preventDefault(); }
    });
  });

  // 2. Mobile menu button
  var menuButton = document.getElementById("menuButton");
  if (menuButton) {
    menuButton.addEventListener("click", function () {
      document.getElementById("sidebar").classList.toggle("open");
    });
  }

  // 3. Auto-hide success / info messages after 6 seconds
  setTimeout(function () {
    document.querySelectorAll(".alert-success, .alert-info").forEach(function (alert) {
      alert.classList.remove("show");
      setTimeout(function () { alert.remove(); }, 400);
    });
  }, 6000);

  // 4. Claim form: when a policy is chosen, show only its members and covered treatments
  var policy = document.getElementById("policy_id");
  var member = document.getElementById("member_id");
  var treatment = document.getElementById("treatment_type");
  var hint = document.getElementById("policyHint");
  if (policy && member) {
    var refresh = function () {
      var chosen = policy.options[policy.selectedIndex];
      var policyId = policy.value;
      Array.from(member.options).forEach(function (option) {
        if (!option.value) { return; }
        var show = option.dataset.policy === policyId;
        option.hidden = !show; option.disabled = !show;
      });
      if (member.selectedOptions[0] && member.selectedOptions[0].disabled) { member.value = ""; }
      if (chosen && chosen.dataset.treatments && treatment) {
        var covered = chosen.dataset.treatments.split(",");
        Array.from(treatment.options).forEach(function (option) {
          if (!option.value) { return; }
          option.disabled = covered.indexOf(option.value) === -1;
        });
        if (treatment.selectedOptions[0] && treatment.selectedOptions[0].disabled) { treatment.value = ""; }
        if (hint) {
          hint.textContent = "Plan covers: " + covered.join(", ") + ". Policy period: " +
            chosen.dataset.start + " to " + chosen.dataset.end + ".";
        }
      } else if (hint) { hint.textContent = ""; }
    };
    policy.addEventListener("change", refresh);
    refresh();
  }
});


/* Claim submissions require every supporting file; drafts may be saved without files. */
document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("claimForm");
  if (!form) return;
  form.addEventListener("submit", function (event) {
    const action = event.submitter ? event.submitter.value : "";
    if (action !== "submit") return;
    const inputs = Array.from(form.querySelectorAll('input[type="file"][name^="file_"]'));
    const missing = inputs.filter((input) => !input.files || input.files.length === 0);
    if (missing.length) {
      event.preventDefault();
      window.alert("Please upload all required documents before submitting your claim.");
      missing[0].focus();
    }
  });
});
