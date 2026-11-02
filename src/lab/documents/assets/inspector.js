class Inspector extends HTMLElement {
  anchor = null;
  activeId = null;
  pinned = false;
  timer = null;
  restoringFocus = false;

  connectedCallback() {
    this.scope = this.closest("lab-worksheet");
    this.content = this.querySelector(".inspector-content");
    this.closeButton = this.querySelector(".inspector-close");
    this.hint = this.querySelector(".inspector-hint");
    this.canHover = matchMedia("(hover: hover) and (pointer: fine)");
    this.events = new AbortController();
    const options = { signal: this.events.signal };

    this.enhanceReferences(this.scope);
    this.scope.querySelector(".reference-details").open = false;
    this.scope.classList.add("interactive");

    this.scope.addEventListener("pointerover", event => {
      if (!this.canHover.matches || event.pointerType !== "mouse" || this.pinned) return;
      if (this.contains(event.target)) {
        clearTimeout(this.timer);
        return;
      }
      const link = event.target.closest("[data-inspect]");
      if (!link || link.contains(event.relatedTarget)) return;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.show(link), this.hidden ? 160 : 0);
    }, options);

    this.scope.addEventListener("pointerout", event => {
      const link = event.target.closest("[data-inspect]");
      if (link && !link.contains(event.relatedTarget)) this.scheduleClose();
    }, options);
    this.addEventListener("pointerleave", () => this.scheduleClose(), options);
    this.addEventListener("pointerenter", () => clearTimeout(this.timer), options);

    document.addEventListener("focusin", event => {
      if (this.restoringFocus || this.contains(event.target)) return;
      const link = this.scope.contains(event.target) && event.target.closest("[data-inspect]");
      if (link) this.show(link);
      else this.close();
    }, options);

    this.scope.addEventListener("click", event => {
      const link = event.target.closest("[data-inspect]");
      if (link) {
        event.preventDefault();
        if (this.pinned && this.activeId === link.dataset.inspect && link === this.anchor) {
          this.close();
        } else {
          this.show(link, true);
          if (event.detail === 0) this.closeButton.focus({ preventScroll: true });
        }
      } else if (this.contains(event.target) && event.target.closest("a")) {
        this.close();
      } else if (this.contains(event.target)) {
        this.pinned = true;
        this.hint.textContent = "Details";
      }
    }, options);

    document.addEventListener("pointerdown", event => {
      const reference = this.scope.contains(event.target) && event.target.closest("[data-inspect]");
      if (!this.contains(event.target) && !reference) this.close();
    }, options);
    this.closeButton.addEventListener("click", event => {
      event.stopPropagation();
      this.close(true);
    }, options);
    this.scope.addEventListener("keydown", event => this.onKeyDown(event), options);
    document.addEventListener("scroll", event => {
      if (this.hidden || this.contains(event.target)) return;
      if (this.pinned || document.activeElement === this.anchor || this.contains(document.activeElement)) {
        this.position();
      } else {
        this.close();
      }
    }, { ...options, capture: true });
    window.addEventListener("resize", () => this.position(), options);
    window.addEventListener("blur", () => this.close(), options);
    window.addEventListener("beforeprint", () => this.close(), options);
    this.resizeObserver = new ResizeObserver(() => this.position());
    this.resizeObserver.observe(this);
  }

  disconnectedCallback() {
    clearTimeout(this.timer);
    this.events.abort();
    this.resizeObserver.disconnect();
  }

  enhanceReferences(root) {
    root.querySelectorAll("[data-inspect]").forEach(link => {
      link.setAttribute("role", "button");
      link.setAttribute("aria-haspopup", "dialog");
      link.setAttribute("aria-controls", this.id);
      link.setAttribute("aria-expanded", "false");
    });
  }

  markReferences() {
    this.scope.querySelectorAll("[data-inspect]").forEach(link => {
      link.classList.toggle("inspect-active", !this.hidden && link.dataset.inspect === this.activeId);
      link.setAttribute("aria-expanded", String(!this.hidden && link === this.anchor));
    });
  }

  show(link, keepOpen = false) {
    clearTimeout(this.timer);
    const card = this.scope.querySelector(`#${CSS.escape(link.dataset.inspect)}`);
    if (!card) return;
    if (!this.contains(link)) this.anchor = link;
    else this.closeButton.focus({ preventScroll: true });
    if (this.activeId !== card.id) {
      const copy = card.cloneNode(true);
      copy.removeAttribute("id");
      this.content.replaceChildren(copy);
      this.enhanceReferences(this.content);
      this.setAttribute("aria-label", `${card.querySelector(".detail-kind").textContent}: ${card.querySelector(".detail-title").textContent}`);
      this.content.scrollTop = 0;
    }
    this.activeId = card.id;
    this.content.querySelectorAll("lab-flow-profile").forEach(profile => {
      profile.selectOperation(link.closest("tr[id^='step-']")?.id);
    });
    this.pinned = keepOpen;
    this.hint.textContent = this.pinned ? "Details" : "Preview · click to keep open";
    this.hidden = false;
    this.markReferences();
    this.position();
  }

  close(restoreFocus = false) {
    clearTimeout(this.timer);
    const returnTo = this.anchor;
    this.hidden = true;
    this.pinned = false;
    this.activeId = null;
    this.markReferences();
    if (restoreFocus && returnTo?.isConnected) {
      this.restoringFocus = true;
      returnTo.focus({ preventScroll: true });
      this.restoringFocus = false;
    }
  }

  scheduleClose() {
    clearTimeout(this.timer);
    if (!this.pinned && document.activeElement !== this.anchor && !this.contains(document.activeElement)) {
      this.timer = setTimeout(() => this.close(), 180);
    }
  }

  position() {
    if (this.hidden || !this.anchor?.isConnected) return;
    const box = this.anchor.getBoundingClientRect();
    const margin = 12;
    const gap = 10;
    let left = box.right + gap;
    if (left + this.offsetWidth > innerWidth - margin) left = box.left - this.offsetWidth - gap;
    let top = box.top;
    if (left < margin) {
      left = Math.max(margin, Math.min(box.left, innerWidth - this.offsetWidth - margin));
      top = box.bottom + gap;
      if (top + this.offsetHeight > innerHeight - margin) top = box.top - this.offsetHeight - gap;
    }
    this.style.left = `${Math.max(margin, left)}px`;
    this.style.top = `${Math.max(margin, Math.min(top, innerHeight - this.offsetHeight - margin))}px`;
  }

  onKeyDown(event) {
    if (event.key === "Escape" && !this.hidden) {
      event.preventDefault();
      this.close(true);
    } else if (event.key === " " && event.target.matches("[data-inspect]")) {
      event.preventDefault();
      event.target.click();
    } else if (event.key === "Tab" && !this.hidden && this.pinned) {
      const inside = [...this.querySelectorAll("button, a, select, summary, [tabindex='0']")]
        .filter(node => node.getClientRects().length);
      if (event.target === this.anchor && !event.shiftKey) {
        event.preventDefault();
        this.closeButton.focus();
      } else if (event.target === this.closeButton && event.shiftKey) {
        event.preventDefault();
        this.close(true);
      } else if (event.target === inside.at(-1) && !event.shiftKey) {
        const outside = [...this.scope.querySelectorAll("a, button, summary, [tabindex='0']")]
          .filter(node => !this.contains(node) && node.getClientRects().length);
        const next = outside[outside.indexOf(this.anchor) + 1];
        if (next) {
          event.preventDefault();
          this.close();
          next.focus();
        }
      }
    }
  }
}

customElements.define("lab-inspector", Inspector);
