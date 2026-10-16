/* Continuous, local QR recognition. No camera images leave the device. */
(() => {
  "use strict";
  class TicketCamera {
    constructor({ video, onRead, onState, canRead = () => true }) {
      this.video = video;
      this.onRead = onRead;
      this.onState = onState;
      this.canRead = canRead;
      this.canvas = document.createElement("canvas");
      this.context = this.canvas.getContext("2d", { willReadFrequently: true });
      this.stream = null;
      this.running = false;
      this.generation = 0;
      this.frame = 0;
      this.detector = null;
      this.release();
    }

    release() {
      this.lastPayload = "";
      this.lastSeenAt = 0;
      this.missingSince = 0;
      this.candidate = "";
      this.candidateCount = 0;
    }

    async start() {
      if (this.running || this.starting) return;
      const generation = ++this.generation;
      this.starting = true;
      this.onState("starting");
      try {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
          throw new Error(
            "Camera access requires HTTPS and a supported browser.",
          );
        }
        if (typeof window.jsQR !== "function")
          throw new Error("QR reader could not load.");
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: {
            facingMode: { ideal: "environment" },
            width: { ideal: 1920 },
            height: { ideal: 1080 },
            frameRate: { ideal: 24, max: 30 },
          },
        });
        if (generation !== this.generation) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        this.stream = stream;
        const track = stream.getVideoTracks()[0];
        try {
          const capabilities = track.getCapabilities?.();
          if (capabilities?.focusMode?.includes("continuous")) {
            await track.applyConstraints({
              advanced: [{ focusMode: "continuous" }],
            });
          }
        } catch (_) {
          /* Autofocus controls are optional on mobile devices. */
        }
        if (generation !== this.generation) return;
        this.video.srcObject = stream;
        this.video.muted = true;
        this.video.setAttribute("playsinline", "");
        await this.video.play();
        if (generation !== this.generation) return;
        if ("BarcodeDetector" in window) {
          try {
            const formats = await BarcodeDetector.getSupportedFormats();
            if (formats.includes("qr_code"))
              this.detector = new BarcodeDetector({ formats: ["qr_code"] });
          } catch (_) {
            this.detector = null;
          }
        }
        if (generation !== this.generation) return;
        this.running = true;
        this.starting = false;
        this.lastVideoTime = -1;
        this.lastFrameAt = 0;
        this.frameCount = 0;
        this.release();
        stream.getVideoTracks().forEach((track) =>
          track.addEventListener(
            "ended",
            () => {
              if (generation === this.generation) this.fail();
            },
            { once: true },
          ),
        );
        this.onState("active");
        this.schedule(generation);
      } catch (_) {
        if (generation === this.generation) this.fail();
      } finally {
        if (generation === this.generation) this.starting = false;
      }
    }

    stop() {
      ++this.generation;
      this.running = false;
      this.starting = false;
      cancelAnimationFrame(this.frame);
      if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
      this.video.pause();
      this.video.srcObject = null;
      this.detector = null;
      this.release();
      this.onState("off");
    }

    fail() {
      this.stop();
      this.onState("error");
    }

    schedule(generation) {
      if (this.running && generation === this.generation) {
        this.frame = requestAnimationFrame((now) => this.tick(now, generation));
      }
    }

    async tick(now, generation) {
      if (!this.running || generation !== this.generation) return;
      if (
        document.hidden ||
        !this.canRead() ||
        now - this.lastFrameAt < 160 ||
        this.video.readyState < 2 ||
        this.video.currentTime === this.lastVideoTime
      ) {
        this.schedule(generation);
        return;
      }
      this.lastFrameAt = now;
      this.lastVideoTime = this.video.currentTime;
      try {
        let value = "";
        if (this.detector) {
          try {
            const codes = await this.detector.detect(this.video);
            value = codes[0]?.rawValue || "";
          } catch (_) {
            this.detector = null;
          }
        }
        if (!value) value = this.decodeCanvas();
        if (generation !== this.generation) return;
        const stamp = performance.now();
        if (value) {
          this.lastSeenAt = stamp;
          this.missingSince = 0;
          if (value !== this.lastPayload) {
            this.candidateCount =
              value === this.candidate ? this.candidateCount + 1 : 1;
            this.candidate = value;
            // Two distinct video frames must agree before submitting a ticket.
            if (this.candidateCount >= 2) {
              this.lastPayload = value;
              this.candidate = "";
              this.candidateCount = 0;
              await this.onRead(value);
            }
          }
        } else {
          this.candidate = "";
          this.candidateCount = 0;
          // A stationary code stays latched indefinitely. Re-arm only once it
          // leaves view, or when the operator explicitly starts the next scan.
          if (!this.missingSince) this.missingSince = stamp;
          if (stamp - this.missingSince > 2200) this.lastPayload = "";
        }
      } catch (_) {
        // A transient video-frame read error must not stop a live camera.
      }
      this.schedule(generation);
    }

    decodeCanvas() {
      const width = this.video.videoWidth;
      const height = this.video.videoHeight;
      if (!width || !height || !this.context) return "";
      ++this.frameCount;
      // Concentrate pixels near the guide for small codes, with regular full
      // frame passes. This also covers codes outside the visible guide.
      const full = this.frameCount % 4 === 0;
      const side = Math.min(width, height) * 0.84;
      const sourceWidth = full ? width : side;
      const sourceHeight = full ? height : side;
      const scale = Math.min(
        1,
        (full ? 1200 : 960) / Math.max(sourceWidth, sourceHeight),
      );
      this.canvas.width = Math.round(sourceWidth * scale);
      this.canvas.height = Math.round(sourceHeight * scale);
      this.context.drawImage(
        this.video,
        (width - sourceWidth) / 2,
        (height - sourceHeight) / 2,
        sourceWidth,
        sourceHeight,
        0,
        0,
        this.canvas.width,
        this.canvas.height,
      );
      const image = this.context.getImageData(
        0,
        0,
        this.canvas.width,
        this.canvas.height,
      );
      const code = window.jsQR(image.data, image.width, image.height, {
        inversionAttempts: "attemptBoth",
      });
      return code?.data || "";
    }
  }
  window.TicketCamera = TicketCamera;
})();
