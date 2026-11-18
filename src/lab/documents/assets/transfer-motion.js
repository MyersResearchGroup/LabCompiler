class TransferMotion extends HTMLElement {
  animations = [];

  connectedCallback() {
    this.board = this.closest(".preparation-plates");
    this.svg = this.querySelector("svg");
    this.routes = this.querySelector("[data-transfer-routes]");
    this.pipette = this.querySelector("[data-motion-pipette]");
    this.liquid = this.querySelector(".pipette-liquid");
    this.drop = this.querySelector("[data-motion-drop]");
    this.reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    this.events = new AbortController();
    const options = { signal: this.events.signal };
    this.board.addEventListener("scroll", () => this.redraw(), { ...options, capture: true });
    this.reducedMotion.addEventListener("change", () => this.redraw(true), options);
    this.resizeObserver = new ResizeObserver(() => this.redraw());
    this.resizeObserver.observe(this.board);
  }

  disconnectedCallback() {
    this.stop();
    this.events.abort();
    this.resizeObserver.disconnect();
  }

  stop() {
    this.animations.forEach(animation => animation.cancel());
    this.animations = [];
  }

  pause() {
    this.animations.forEach(animation => {
      if (animation.playState === "running") animation.pause();
    });
  }

  setTime(time) {
    this.animations.forEach(animation => { animation.currentTime = time; });
  }

  capturePosition() {
    if (this.hidden) return null;
    const matrix = this.pipette.getScreenCTM();
    return matrix ? new DOMPoint(0, 0).matrixTransform(matrix) : null;
  }

  redraw(force = false) {
    if (!this.frame || this.hidden) return;
    if (force || this.geometry(this.frame).key !== this.geometryKey) {
      const animated = this.animations.length > 0;
      const progress = animated ? Number(this.animations.at(-1).currentTime ?? 0) / this.duration : 1;
      this.showFrame(this.frame, animated);
      this.pause();
      this.setTime(progress * this.duration);
    }
  }

  point(anchor) {
    const well = this.board.querySelector(`[data-map-well="${CSS.escape(anchor)}"] .well-outline`);
    const bounds = well.getBoundingClientRect();
    const board = this.board.getBoundingClientRect();
    return { x: bounds.x + bounds.width / 2 - board.x, y: bounds.y + bounds.height / 2 - board.y };
  }

  geometry(frame) {
    const board = this.board.getBoundingClientRect();
    const anchors = frame.lanes
      ? frame.destinations.flatMap((destination, index) => [frame.sources[index], destination])
      : frame.refill_between_destinations
      ? frame.destinations.flatMap(destination => [frame.source, destination])
      : frame.source ? [frame.source, ...frame.destinations] : frame.active;
    const points = anchors.map(anchor => this.point(anchor));
    return { width: board.width, height: board.height, points,
      key: [board.width, board.height, ...points.flatMap(point => [point.x, point.y])].join(",") };
  }

  route(start, end, returning = false) {
    const middle = (start.x + end.x) / 2;
    const sameRow = Math.abs(start.y - end.y) < 20;
    const lift = sameRow ? Math.min(40, Math.abs(end.x - start.x) / 2) : 0;
    const d = `M${start.x} ${start.y} C${sameRow ? start.x : middle} ${start.y - lift} ${sameRow ? end.x : middle} ${end.y - lift} ${end.x} ${end.y}`;
    const halo = document.createElementNS("http://www.w3.org/2000/svg", "path");
    halo.setAttribute("d", d);
    halo.setAttribute("class", "transfer-route-halo");
    const path = halo.cloneNode();
    path.setAttribute("class", "transfer-route");
    if (returning) {
      halo.classList.add("transfer-return");
      path.classList.add("transfer-return");
    }
    this.routes.append(halo, path);
    return path;
  }

  showFrame(frame, animate = false, origin = null) {
    this.stop();
    this.frame = frame;
    this.duration = Math.max(900, frame.lanes
      ? 900
      : frame.refill_between_destinations
      ? frame.destinations.length * 900
      : frame.source ? 250 + frame.destinations.length * 450 : frame.mix_cycles * 400);
    this.routes.replaceChildren();
    this.hidden = !frame.source && !frame.mix_cycles;
    if (this.hidden) return;
    const geometry = this.geometry(frame);
    this.geometryKey = geometry.key;
    this.svg.setAttribute("viewBox", `0 0 ${geometry.width} ${geometry.height}`);
    const points = geometry.points;
    const board = this.board.getBoundingClientRect();
    const previous = origin ? { x: origin.x - board.x, y: origin.y - board.y + 12 } : null;
    const distance = previous ? Math.hypot(previous.x - points[0].x, previous.y - points[0].y) : 0;
    const transitionDuration = animate && !this.reducedMotion.matches && distance > .5
      ? Math.min(240, Math.max(100, distance / 3)) : 0;
    const paths = points.slice(1).map((point, index) =>
      this.route(points[index], point, (frame.refill_between_destinations || frame.lanes) && index % 2 === 1));
    const last = points.at(-1);
    this.pipette.style.transform = `translate(${last.x}px, ${last.y - 12}px)`;
    if (!animate || this.reducedMotion.matches) return;

    const duration = this.duration;
    this.duration += transitionDuration;
    const position = (point, offset, dip = false) => ({
      transform: `translate(${point.x}px, ${point.y - (dip ? 2 : 12)}px)`, offset: Math.min(1, offset),
    });
    let keyframes;
    const liquidFrames = [{ transform: "scaleY(0)", opacity: 0, offset: 0 }];
    const travel = (path, start, end) => {
      const length = path.getTotalLength();
      for (let sample = 1; sample <= 20; sample++) {
        keyframes.push(position(path.getPointAtLength(length * sample / 20), start + (end - start) * sample / 20));
      }
    };
    const dispense = (destination, start, end) => {
      this.animations.push(this.drop.animate([
        { transform: `translate(${destination.x}px, ${destination.y - 8}px)`, opacity: 0 },
        { transform: `translate(${destination.x}px, ${destination.y - 4}px)`, opacity: 1, offset: .4 },
        { transform: `translate(${destination.x}px, ${destination.y}px)`, opacity: 0 },
      ], { duration: duration * (end - start), delay: transitionDuration + duration * start }));
    };
    if (frame.mix_cycles) {
      keyframes = [position(points[0], 0)];
      for (let cycle = 0; cycle < frame.mix_cycles; cycle++) {
        keyframes.push(position(points[0], (cycle + .5) / frame.mix_cycles, true));
        keyframes.push(position(points[0], (cycle + 1) / frame.mix_cycles));
        liquidFrames.push({ transform: "scaleY(1)", opacity: 1, offset: (cycle + .5) / frame.mix_cycles });
        liquidFrames.push({ transform: "scaleY(0)", opacity: 0, offset: (cycle + 1) / frame.mix_cycles });
      }
    } else if (frame.refill_between_destinations) {
      keyframes = [position(points[0], 0)];
      const interval = 1 / frame.destinations.length;
      frame.destinations.forEach((_, index) => {
        const start = index * interval;
        const source = points[index * 2];
        const destination = points[index * 2 + 1];
        if (index) travel(paths[index * 2 - 1], start, start + interval * .25);
        keyframes.push(position(source, start + interval * .25));
        keyframes.push(position(source, start + interval * .33, true));
        keyframes.push(position(source, start + interval * .4));
        liquidFrames.push({ transform: "scaleY(0)", opacity: 0, offset: start + interval * .25 });
        liquidFrames.push({ transform: "scaleY(1)", opacity: 1, offset: start + interval * .4 });
        travel(paths[index * 2], start + interval * .4, start + interval * .8);
        keyframes.push(position(destination, start + interval * .9, true));
        keyframes.push(position(destination, start + interval));
        liquidFrames.push({ transform: "scaleY(1)", opacity: 1, offset: start + interval * .8 });
        liquidFrames.push({ transform: "scaleY(0)", opacity: 0, offset: Math.min(1, start + interval) });
        dispense(destination, start + interval * .8, start + interval);
      });
    } else {
      keyframes = [position(points[0], 0), position(points[0], .08, true), position(points[0], .16)];
      liquidFrames.push({ transform: "scaleY(0)", opacity: 0, offset: .08 });
      liquidFrames.push({ transform: "scaleY(1)", opacity: 1, offset: .16 });
      paths.forEach((path, index) => {
        const start = .16 + index / paths.length * .84;
        const interval = .84 / paths.length;
        travel(path, start, start + interval * .7);
        keyframes.push(position(points[index + 1], start + interval * .85, true));
        keyframes.push(position(points[index + 1], start + interval));
        liquidFrames.push({ transform: `scaleY(${1 - index / paths.length})`, opacity: 1, offset: start + interval * .7 });
        liquidFrames.push({ transform: `scaleY(${1 - (index + 1) / paths.length})`, opacity: index === paths.length - 1 ? 0 : 1, offset: Math.min(1, start + interval) });
        dispense(points[index + 1], start + interval * .7, start + interval);
      });
    }
    if (transitionDuration) {
      const connection = this.route(previous, points[0], true);
      const length = connection.getTotalLength();
      const travelFrames = Array.from({length: 21}, (_, sample) => {
        const fraction = sample / 20;
        return position(connection.getPointAtLength(length * fraction), transitionDuration / this.duration * fraction);
      });
      const offset = keyframe => ({ ...keyframe, offset: (transitionDuration + keyframe.offset * duration) / this.duration });
      keyframes = [...travelFrames, ...keyframes.map(offset)];
      liquidFrames.splice(0, liquidFrames.length,
        { transform: "scaleY(0)", opacity: 0, offset: 0 }, ...liquidFrames.map(offset));
    }
    this.animations.push(this.pipette.animate(keyframes, { duration: this.duration, easing: "linear" }));
    this.animations.push(this.liquid.animate(liquidFrames, { duration: this.duration, easing: "linear" }));
  }
}

customElements.define("lab-transfer-motion", TransferMotion);
