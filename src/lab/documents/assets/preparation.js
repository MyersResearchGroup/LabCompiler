class PreparationPreview extends HTMLElement {
  index = 0;
  progress = 1;
  playing = false;
  animationFrame = null;
  lastTime = null;

  connectedCallback() {
    this.frames = JSON.parse(this.querySelector("[data-preparation-frames]").textContent);
    this.scope = this.closest("lab-worksheet");
    this.slider = this.querySelector("[data-preview-position]");
    this.playButton = this.querySelector("[data-play]");
    this.motion = this.querySelector("lab-transfer-motion");
    this.wells = [...this.querySelectorAll("[data-map-well]")];
    this.events = new AbortController();
    const options = { signal: this.events.signal };

    this.querySelector(".preparation-controls").hidden = false;
    this.slider.addEventListener("input", () => this.scrub(Number(this.slider.value)), options);
    this.slider.addEventListener("keydown", event => {
      const current = Number(this.slider.value);
      const positions = {
        ArrowLeft: Math.ceil(current) - 1, ArrowDown: Math.ceil(current) - 1,
        ArrowRight: Math.floor(current) + 1, ArrowUp: Math.floor(current) + 1,
        PageDown: Math.ceil(current) - 10, PageUp: Math.floor(current) + 10,
        Home: 0, End: this.frames.length - 1,
      };
      if (event.key in positions) {
        event.preventDefault();
        this.seek(positions[event.key]);
      }
    }, options);
    this.querySelector("[data-previous]").addEventListener("click", event => this.seek(this.index - 1, event.detail > 0), options);
    this.querySelector("[data-next]").addEventListener("click", event => this.seek(this.index + 1, event.detail > 0), options);
    this.querySelector("[data-first]").addEventListener("click", () => this.seek(0), options);
    this.querySelector("[data-last]").addEventListener("click", event => this.seek(this.frames.length - 1, event.detail > 0), options);
    this.playButton.addEventListener("click", () => this.playing ? this.pause() : this.play(), options);

    this.scope.addEventListener("click", event => {
      const link = event.target.closest("[data-preview-step]");
      if (!link) return;
      event.preventDefault();
      this.seek(Number(link.dataset.previewStep), event.detail > 0);
      this.scrollIntoView({ block: "start" });
      this.slider.focus({ preventScroll: true });
    }, options);
    this.scope.addEventListener("pointerdown", event => {
      if (event.target.closest("[data-inspect]")) this.pause();
    }, options);
    this.scope.addEventListener("focusin", event => {
      if (event.target.closest("[data-inspect]")) this.pause();
    }, options);
    this.addEventListener("keydown", event => this.navigatePlate(event), options);
    this.querySelectorAll(".plate-map").forEach(plate => {
      plate.querySelectorAll("[data-map-well]").forEach((well, index) => {
        well.setAttribute("tabindex", index === 0 ? "0" : "-1");
      });
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.pause();
    }, options);
    window.addEventListener("beforeprint", () => {
      this.printIndex = this.index;
      this.seek(0);
    }, options);
    window.addEventListener("afterprint", () => {
      this.seek(this.printIndex ?? this.index);
    }, options);
    this.visibilityObserver = new IntersectionObserver(entries => {
      if (!entries[0].isIntersecting) this.pause();
    });
    this.visibilityObserver.observe(this);
    this.showFrame(this.index);
  }

  disconnectedCallback() {
    this.pause();
    this.events.abort();
    this.visibilityObserver.disconnect();
  }

  seek(index, animate = false) {
    this.pause();
    this.showFrame(index, animate);
    if (animate) this.play(false);
  }

  scrub(position) {
    this.pause();
    position = Math.max(0, Math.min(position, this.frames.length - 1));
    this.showFrame(Math.ceil(position), position > 0, false);
    this.progress = position === 0 ? 1 : position - (this.index - 1);
    this.motion.setTime(this.progress * this.motion.duration);
    this.updatePosition();
  }

  showFrame(index, animate = false, connect = true) {
    const origin = animate && connect ? this.motion.capturePosition() : null;
    this.index = Math.max(0, Math.min(Math.round(index), this.frames.length - 1));
    this.currentFrame = this.frames[this.index];
    this.volumes = new Map(this.currentFrame.volumes.map(well => [well.anchor, well]));
    const frame = this.currentFrame;
    const label = this.index === 0 ? "Initial loads" : `After step ${this.index} of ${this.frames.length - 1}`;
    this.querySelector("[data-preview-label]").textContent = label;
    this.querySelector("[data-preview-description]").textContent = frame.description;
    const procedure = this.querySelector("[data-preview-procedure]");
    procedure.hidden = this.index === 0;
    procedure.href = `#step-${this.index}`;
    this.querySelector("[data-previous]").disabled = this.index === 0;
    this.querySelector("[data-next]").disabled = this.index === this.frames.length - 1;
    this.wells.forEach(well => {
      const anchor = well.dataset.mapWell;
      const state = this.volumes.get(anchor);
      well.querySelector(".well-liquid").setAttribute("r", state.radius);
      well.classList.toggle("is-source", anchor === frame.source || (frame.sources || []).includes(anchor));
      well.classList.toggle("is-destination", frame.destinations.includes(anchor));
      well.classList.toggle("is-active", frame.active.includes(anchor));
      const description = `${well.dataset.wellLabel} · ${state.volume} µL · ${label}`;
      well.setAttribute("aria-label", description);
      well.querySelector("title").textContent = description;
    });
    this.scope.querySelectorAll(".procedure tr[id]").forEach(row => {
      const current = row.id === `step-${this.index}`;
      row.classList.toggle("preview-current", current);
      const link = row.querySelector("[data-preview-step]");
      if (current) link?.setAttribute("aria-current", "step");
      else link?.removeAttribute("aria-current");
    });
    this.scope.dispatchEvent(new CustomEvent("lab:previewchange", { detail: frame }));
    this.motion.showFrame(frame, animate, origin);
    this.motion.pause();
    this.progress = animate ? 0 : 1;
    this.motion.setTime(this.progress * this.motion.duration);
    this.updatePosition();
  }

  updatePosition() {
    this.slider.value = String(this.index === 0 ? 0 : this.index - 1 + this.progress);
    const label = this.index === 0 ? "Initial loads"
      : this.progress === 1 ? `After step ${this.index} of ${this.frames.length - 1}`
      : `Step ${this.index} of ${this.frames.length - 1}, ${Math.round(this.progress * 100)}% of preview`;
    this.slider.setAttribute("aria-valuetext", label);
  }

  play(continuous = true) {
    if (this.playing) return;
    if (this.index === this.frames.length - 1 && this.progress === 1) this.showFrame(0);
    this.continuous = continuous;
    this.playing = true;
    this.classList.add("playing");
    this.playButton.textContent = "Pause";
    this.playButton.setAttribute("aria-label", "Pause preparation preview");
    if (this.progress === 1) this.showFrame(this.index + 1, true);
    this.lastTime = null;
    this.animationFrame = requestAnimationFrame(time => this.tick(time));
  }

  tick(time) {
    if (!this.playing) return;
    const elapsed = this.lastTime === null ? 0 : time - this.lastTime;
    this.lastTime = time;
    this.progress = Math.min(1, this.progress + elapsed / this.motion.duration);
    this.motion.setTime(this.progress * this.motion.duration);
    this.updatePosition();
    if (this.progress === 1) {
      if (!this.continuous || this.index === this.frames.length - 1) {
        this.pause();
        return;
      }
      this.showFrame(this.index + 1, true);
    }
    this.animationFrame = requestAnimationFrame(next => this.tick(next));
  }

  pause() {
    cancelAnimationFrame(this.animationFrame);
    this.animationFrame = null;
    this.lastTime = null;
    this.playing = false;
    this.motion.pause();
    this.classList.remove("playing");
    this.playButton.textContent = "Play";
    this.playButton.setAttribute("aria-label", "Play preparation preview");
  }

  navigatePlate(event) {
    const well = event.target.closest("[data-map-well]");
    if (!well) return;
    const siblings = [...well.closest(".plate-map").querySelectorAll("[data-map-well]")];
    const row = Number(well.dataset.row);
    const column = Number(well.dataset.column);
    let candidates = [];
    switch (event.key) {
      case "ArrowRight": candidates = siblings.filter(node => Number(node.dataset.row) === row && Number(node.dataset.column) > column); break;
      case "ArrowLeft": candidates = siblings.filter(node => Number(node.dataset.row) === row && Number(node.dataset.column) < column).reverse(); break;
      case "ArrowDown": candidates = siblings.filter(node => Number(node.dataset.column) === column && Number(node.dataset.row) > row); break;
      case "ArrowUp": candidates = siblings.filter(node => Number(node.dataset.column) === column && Number(node.dataset.row) < row).reverse(); break;
      case "Home": candidates = siblings; break;
      case "End": candidates = siblings.slice().reverse(); break;
      default: return;
    }
    event.preventDefault();
    const next = candidates[0];
    if (next) {
      siblings.forEach(node => node.setAttribute("tabindex", node === next ? "0" : "-1"));
      next.focus({ preventScroll: true });
    }
  }
}

class WellVolume extends HTMLElement {
  connectedCallback() {
    const scope = this.closest("lab-worksheet");
    const preview = scope.querySelector("lab-preparation");
    this.events = new AbortController();
    scope.addEventListener("lab:previewchange", event => this.showFrame(event.detail), { signal: this.events.signal });
    if (preview?.currentFrame) this.showFrame(preview.currentFrame);
  }

  disconnectedCallback() {
    this.events.abort();
  }

  showFrame(frame) {
    const state = frame.volumes.find(well => well.anchor === this.dataset.location);
    if (!state) return;
    this.querySelector("[data-volume-value]").textContent = state.volume;
    const context = this.querySelector("[data-volume-context]");
    if (context) context.textContent = frame.number === 0 ? "Initial loads" : `After step ${frame.number}`;
  }
}

customElements.define("lab-preparation", PreparationPreview);
customElements.define("lab-well-volume", WellVolume);
