class FlowProfile extends HTMLElement {
  connectedCallback() {
    this.events = new AbortController();
    const options = { signal: this.events.signal };
    this.select = this.querySelector("[data-profile-select]");
    this.select.disabled = false;
    this.select.addEventListener("change", () => this.selectOperation(), options);
    for (const event of ["pointerover", "focusin"]) {
      this.addEventListener(event, event => {
        const phase = event.target.closest("[data-flow-description]");
        if (phase) {
          phase.closest(".flow-chart").querySelector("output").textContent = phase.dataset.flowDescription;
        }
      }, options);
    }
    this.selectOperation();
  }

  disconnectedCallback() {
    this.events.abort();
  }

  selectOperation(operation) {
    if (operation && [...this.select.options].some(option => option.value === operation)) {
      this.select.value = operation;
    }
    this.querySelectorAll("[data-flow-profile]").forEach(profile => {
      profile.hidden = profile.dataset.flowProfile !== this.select.value;
    });
  }
}

customElements.define("lab-flow-profile", FlowProfile);
