(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let csrf = "",
    user = null,
    appMode = document.body.dataset.mode || "test",
    activeTab = "scan",
    verifying = false;
  let cardsLoaded = "",
    sampleRequest = 0,
    audio = null;
  const kindNames = {
    WHITE: "WHITE TICKET",
    MAIN: "MAIN TICKET",
    "VIP CREATIVITY": "VIP CREATIVITY LOUNGE",
    "VIP CULTURE": "VIP CULTURE LOUNGE",
    VIP_CREATIVITY: "VIP CREATIVITY LOUNGE",
    VIP_CULTURE: "VIP CULTURE LOUNGE",
    INVALID: "INVALID QR CODE",
    "MAIN STUB": "MAIN TICKET STUB",
  };
  const initialStatuses = {
    unused: "INITIALLY VALID",
    used: "INITIALLY USED",
    cancelled: "CANCELLED",
    invalid: "INVALID CODE",
    "same-as-02": "SAME CODE AS SAMPLE 2",
  };

  async function api(path, method = "GET", body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(path, {
        method,
        credentials: "same-origin",
        signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-CSRF": csrf },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await response
        .json()
        .catch(() => ({ message: "Unable to read the server response." }));
      if (!response.ok) {
        if (response.status === 401 && user && path !== "/api/login") {
          signedOut("Your session has expired. Please sign in again.");
        }
        throw new Error(data.message || "The request could not be completed.");
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError" || error instanceof TypeError) {
        throw new Error(
          "Connection interrupted. Check recent records before retrying.",
        );
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function time(value, compact = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return (
      new Intl.DateTimeFormat("en-GB", {
        timeZone: "Africa/Freetown",
        dateStyle: compact ? "short" : "medium",
        timeStyle: "medium",
      }).format(date) + (compact ? "" : " GMT")
    );
  }

  const camera = new window.TicketCamera({
    video: $("cameraVideo"),
    canRead: () => !verifying && Boolean(user),
    onRead: (code) => verify({ code }, "/api/scan"),
    onState: (state) => {
      const live = state === "active",
        starting = state === "starting";
      $("cameraVideo").hidden = !live;
      $("cameraPlaceholder").hidden = live;
      $("cameraPreview").classList.toggle("is-active", live);
      $("cameraState").textContent = {
        active: "SCANNING…",
        starting: "STARTING…",
        off: "CAMERA OFF",
        error: "CAMERA ERROR",
      }[state];
      $("cameraBadge").className =
        "camera-badge" +
        (live ? " is-live" : state === "error" ? " is-error" : "");
      $("startCamera").disabled = live || starting;
      $("stopCamera").disabled = !live && !starting;
      $("cameraMessage").classList.toggle("is-error", state === "error");
      $("cameraMessage").textContent =
        state === "error"
          ? "Camera unavailable. Please check permissions or use manual entry."
          : starting
            ? "Allow camera access when your browser asks. Opening the rear camera…"
            : live
              ? "Scanning… Hold steady. After a result, remove this ticket before scanning the next."
              : "Use the rear camera. Keep the ticket steady and avoid glare.";
    },
  });

  function readPreferences() {
    try {
      const prefs = JSON.parse(localStorage.getItem("onrf2.feedback") || "{}");
      if (typeof prefs.sound === "boolean")
        $("soundToggle").checked = prefs.sound;
      if (typeof prefs.vibration === "boolean")
        $("vibrationToggle").checked = prefs.vibration;
      const gate = localStorage.getItem("onrf2.gate");
      if ([...$("gate").options].some((option) => option.value === gate))
        $("gate").value = gate;
    } catch (_) {
      /* Storage is optional in private browsing. */
    }
    if (!("vibrate" in navigator)) {
      $("vibrationToggle").checked = false;
      $("vibrationToggle").disabled = true;
      $("vibrationToggle").parentElement.title =
        "Vibration is unavailable in this browser.";
    }
  }

  function unlockAudio() {
    if (!$("soundToggle").checked) return;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (Audio && !audio) audio = new Audio();
      if (audio?.state === "suspended") audio.resume().catch(() => {});
    } catch (_) {
      /* Visual feedback always remains available. */
    }
  }

  function feedback(granted) {
    try {
      if ($("soundToggle").checked && audio?.state === "running") {
        const notes = granted ? [740, 1040] : [230, 180];
        notes.forEach((frequency, index) => {
          const oscillator = audio.createOscillator(),
            gain = audio.createGain();
          const start = audio.currentTime + index * 0.13;
          oscillator.type = granted ? "sine" : "triangle";
          oscillator.frequency.value = frequency;
          gain.gain.setValueAtTime(0, start);
          gain.gain.linearRampToValueAtTime(0.09, start + 0.015);
          gain.gain.exponentialRampToValueAtTime(0.001, start + 0.12);
          oscillator.connect(gain);
          gain.connect(audio.destination);
          oscillator.start(start);
          oscillator.stop(start + 0.13);
        });
      }
      if ($("vibrationToggle").checked && navigator.vibrate)
        navigator.vibrate(granted ? 70 : [100, 60, 100]);
    } catch (_) {
      /* Mobile browsers may suppress audio or haptics. */
    }
  }

  function signedIn(data) {
    csrf = data.csrf;
    appMode = data.mode || document.body.dataset.mode || "test";
    user = { role: data.role, username: data.username };
    $("password").value = "";
    $("loginMessage").textContent = "";
    $("loginPage").hidden = true;
    $("portal").hidden = false;
    $("userTools").hidden = false;
    $("roleLabel").textContent = data.role.toUpperCase();
    $("adminTab").hidden = data.role !== "admin";
    $("samplesTab").hidden = appMode === "production";
    $("resetData").hidden = appMode === "production";
    tab(
      ["samples", "admin"].includes(location.hash.slice(1))
        ? location.hash.slice(1)
        : "scan",
    );
  }

  function signedOut(message = "") {
    camera.stop();
    csrf = "";
    user = null;
    cardsLoaded = "";
    ++sampleRequest;
    $("sampleCollection").value = "alphanumeric-20";
    $("sampleCards").replaceChildren();
    $("activityBody").replaceChildren();
    $("portal").hidden = true;
    $("userTools").hidden = true;
    $("loginPage").hidden = false;
    $("password").value = "";
    $("loginMessage").textContent = message;
    ready();
  }

  async function signout() {
    camera.stop();
    try {
      await api("/api/logout", "POST", {});
      signedOut();
      history.replaceState(null, "", location.pathname);
      $("username").focus();
    } catch (error) {
      if (user) {
        if (activeTab === "admin")
          $("adminMessage").textContent = "Sign out failed. " + error.message;
        else {
          tab("scan");
          showError("SIGN OUT FAILED", error.message);
        }
      }
    }
  }

  function tab(name) {
    if (!user) return;
    if (
      !["scan", "samples", "admin"].includes(name) ||
      (appMode === "production" && name === "samples") ||
      (name === "admin" && user.role !== "admin")
    )
      name = "scan";
    if (name !== "scan") camera.stop();
    activeTab = name;
    for (const section of ["scan", "samples", "admin"])
      $(section + "Page").hidden = name !== section;
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const selected = button.dataset.tab === name;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (location.hash !== "#" + name)
      history.replaceState(null, "", "#" + name);
    if (name === "samples") loadSamples();
    if (name === "admin") loadRecords();
  }

  function symbol(kind) {
    const paths = {
      ready: "M13 6H6v7m21-7h7v7M6 27v7h7m21-7v7h-7M14 20h12M20 14v12",
      good: "m10 20 7 7 14-15",
      bad: "m12 12 16 16M28 12 12 28",
      busy: "M20 9v12l7 4M8 11a16 16 0 1 0 12-6",
    };
    // All markup here is fixed artwork, never ticket or server content.
    $("resultSymbol").innerHTML =
      '<svg viewBox="0 0 40 40"><path d="' + paths[kind] + '"/></svg>';
  }

  function ready() {
    document.body.classList.remove("has-result");
    $("resultCard").className = "result-card is-idle";
    $("resultKicker").textContent = "LET THE GOOD TIMES IN";
    $("resultTitle").textContent = "Ready to scan";
    $("resultMessage").textContent =
      "Your ticket verification result will appear here.";
    $("resultDetails").hidden = true;
    $("resultDetails").replaceChildren();
    $("nextTicket").hidden = true;
    symbol("ready");
  }

  function resultLayout(state, title, message, kicker, icon) {
    document.body.classList.add("has-result");
    $("resultCard").className = "result-card " + state;
    $("resultTitle").textContent = title;
    $("resultMessage").textContent = message;
    $("resultKicker").textContent = kicker;
    $("resultDetails").hidden = true;
    $("resultDetails").replaceChildren();
    $("nextTicket").hidden = state === "is-busy";
    symbol(icon);
  }

  function details(items) {
    for (const [label, value] of items) {
      const term = document.createElement("dt"),
        description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value || "—";
      $("resultDetails").append(term, description);
    }
    $("resultDetails").hidden = false;
  }

  function showResult(data) {
    const granted = data.result === "ACCESS GRANTED",
      duplicate = data.result === "ALREADY SCANNED";
    const message = granted
      ? "Valid Ticket"
      : duplicate
        ? "This ticket has already been used."
        : data.reason === "CANCELLED"
          ? "This ticket has been cancelled."
          : "Invalid ticket. Please contact event staff.";
    resultLayout(
      granted ? "is-good" : "is-bad",
      data.result,
      message,
      granted ? "WELCOME TO ONE NATION" : "DO NOT ADMIT",
      granted ? "good" : "bad",
    );
    if (data.ticket) {
      const ticket = data.ticket;
      const lines = [
        ["Ticket No.", ticket.serial],
        ["Ticket Type", kindNames[ticket.kind] || ticket.kind],
      ];
      if (granted)
        lines.push(
          ["Entry Time", time(ticket.first_time)],
          ["Gate", ticket.first_gate],
        );
      if (duplicate)
        lines.push(
          ["First Entry", time(ticket.first_time)],
          ["First Gate", ticket.first_gate],
          ["Checked By", ticket.first_user],
        );
      details(lines);
    }
    feedback(granted);
  }

  function showError(title, message) {
    resultLayout("is-error", title, message, "NO ENTRY CONFIRMED", "bad");
  }

  async function verify(body, path) {
    if (verifying || !user) return;
    verifying = true;
    $("verifyButton").disabled = true;
    document
      .querySelectorAll(".simulate-button")
      .forEach((button) => (button.disabled = true));
    resultLayout(
      "is-busy",
      "VERIFYING TICKET…",
      "Checking this ticket with the secure entry system.",
      "PLEASE WAIT",
      "busy",
    );
    if (window.matchMedia("(max-width:1023px)").matches)
      $("resultColumn").scrollIntoView({ block: "start", behavior: "instant" });
    try {
      if (!navigator.onLine)
        throw new Error(
          "You are offline. Reconnect before verifying a ticket.",
        );
      showResult(await api(path, "POST", { ...body, gate: $("gate").value }));
      $("ticketCode").value = "";
    } catch (error) {
      showError("VERIFICATION UNAVAILABLE", error.message);
    } finally {
      verifying = false;
      $("verifyButton").disabled = false;
      document
        .querySelectorAll(".simulate-button")
        .forEach((button) => (button.disabled = false));
    }
  }

  async function loadSamples() {
    if (appMode === "production") return;
    const collection = $("sampleCollection").value;
    if (cardsLoaded === collection) return;
    cardsLoaded = "";
    const requestNumber = ++sampleRequest;
    $("sampleCards").textContent = "Loading test samples…";
    $("printSamples").disabled = true;
    try {
      const data = await api(
        collection === "original"
          ? "/api/samples"
          : "/api/collections/alphanumeric-20/samples",
      );
      if (!user || requestNumber !== sampleRequest || $("sampleCollection").value !== collection) return;
      $("sampleCounts").textContent =
        data.samples.length + " SAMPLES · " + data.ticket_count + " TEST TICKETS";
      $("sampleCards").replaceChildren();
      data.samples.forEach((sample) => {
        const card = document.createElement("article");
        const color = sample.kind.includes("WHITE")
          ? "white"
          : sample.kind.includes("CREATIVITY")
            ? "creativity"
            : sample.kind.includes("CULTURE")
              ? "culture"
              : sample.kind === "INVALID"
                ? "invalid"
                : "main";
        card.className = "sample-card " + color;
        const top = document.createElement("div");
        top.className = "sample-card-top";
        const number = document.createElement("span");
        number.className = "sample-number";
        number.textContent =
          "SAMPLE " + String(sample.i).padStart(2, "0") + " / ONRF VOL.2";
        const title = document.createElement("h2");
        title.textContent = sample.title;
        const edition = document.createElement("p");
        edition.textContent = "ONE NATION REGGAE FESTIVAL";
        top.append(number, title, edition);
        const content = document.createElement("div");
        content.className = "sample-body";
        const status = document.createElement("span");
        status.className = "sample-status " + sample.state;
        const dot = document.createElement("span");
        dot.className = "status-dot";
        status.append(
          dot,
          document.createTextNode(initialStatuses[sample.state]),
        );
        const image = document.createElement("img");
        image.className = "sample-qr";
        image.src = sample.qr;
        image.alt =
          "Scannable test QR for " +
          sample.title +
          ", " +
          sample.serial;
        image.width = 250;
        image.height = 250;
        const serial = document.createElement("div");
        serial.className = "sample-serial";
        serial.textContent = sample.serial;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button simulate-button";
        button.textContent = "SIMULATE SCAN";
        button.onclick = () => {
          unlockAudio();
          tab("scan");
          verify({}, sample.scan);
        };
        content.append(status, image, serial, button);
        card.append(top, content);
        $("sampleCards").append(card);
      });
      cardsLoaded = collection;
    } catch (error) {
      if (requestNumber === sampleRequest)
        $("sampleCards").textContent = error.message;
    } finally {
      if (requestNumber === sampleRequest) $("printSamples").disabled = false;
    }
  }

  async function loadRecords() {
    if (user?.role !== "admin") return;
    $("refreshRecords").disabled = true;
    $("adminMessage").textContent = "";
    try {
      const data = await api("/api/summary");
      if (!user) return;
      for (const [id, key] of [
        ["statTotal", "total"],
        ["statChecked", "checked_in"],
        ["statUnused", "unused"],
        ["statRejected", "rejected"],
      ]) {
        $(id).textContent = Number(data.stats[key]).toLocaleString("en-GB");
      }
      $("ticketCatalogCounts").textContent = appMode === "production"
        ? "Official ticket register · All gates"
        : data.stats.total + " unique tickets · " + data.catalog.samples + " QR samples";
      $("initiallyUsedCount").textContent = appMode === "production"
        ? "Live entries across every gate"
        : "Includes " + data.catalog.initially_used + " initially used samples";
      $("recordsUpdated").textContent =
        "Updated " + time(data.updated_at, true) + " GMT · Latest 100 scans";
      $("activityBody").replaceChildren();
      if (!data.logs.length) {
        const row = document.createElement("tr"),
          cell = document.createElement("td");
        cell.colSpan = 6;
        cell.className = "empty-table";
        cell.textContent =
          "No scans yet. Your next welcome starts at the scanner.";
        row.append(cell);
        $("activityBody").append(row);
      }
      data.logs.forEach((log) => {
        const row = document.createElement("tr");
        const values = [
          time(log.time, true),
          log.serial || "UNKNOWN",
          kindNames[log.kind] || "UNKNOWN",
          log.outcome,
          log.gate,
          log.operator,
        ];
        values.forEach((value, index) => {
          const cell = document.createElement("td");
          if (index === 3) {
            const pill = document.createElement("span");
            pill.className =
              "outcome-pill" + (value === "ACCESS GRANTED" ? "" : " denied");
            pill.textContent = value;
            cell.append(pill);
          } else cell.textContent = value;
          row.append(cell);
        });
        $("activityBody").append(row);
      });
    } catch (error) {
      $("adminMessage").textContent = error.message;
    } finally {
      $("refreshRecords").disabled = false;
    }
  }

  async function resetData() {
    $("confirmReset").disabled = true;
    try {
      const data = await api("/api/reset", "POST", {
        confirmation: "RESET TEST DATA",
      });
      $("resetDialog").close();
      camera.release();
      ready();
      await loadRecords();
      $("adminMessage").textContent = data.message;
    } catch (error) {
      $("resetDialog").close();
      $("adminMessage").textContent = error.message;
    } finally {
      $("confirmReset").disabled = false;
    }
  }

  $("loginForm").onsubmit = async (event) => {
    event.preventDefault();
    $("loginButton").disabled = true;
    $("loginMessage").textContent = "Signing in…";
    try {
      signedIn(
        await api("/api/login", "POST", {
          username: $("username").value,
          password: $("password").value,
        }),
      );
    } catch (error) {
      $("loginMessage").textContent = error.message;
    } finally {
      $("loginButton").disabled = false;
    }
  };
  $("togglePassword").onclick = () => {
    const visible = $("password").type === "password";
    $("password").type = visible ? "text" : "password";
    $("togglePassword").textContent = visible ? "Hide" : "Show";
    $("togglePassword").setAttribute(
      "aria-label",
      visible ? "Hide password" : "Show password",
    );
  };
  document
    .querySelectorAll("[data-tab]")
    .forEach((button) => (button.onclick = () => tab(button.dataset.tab)));
  window.addEventListener("hashchange", () => {
    if (user) tab(location.hash.slice(1));
  });
  $("startCamera").onclick = () => {
    unlockAudio();
    if (window.matchMedia("(max-width:1023px)").matches)
      $("scannerCard").scrollIntoView({ block: "start" });
    camera.start();
  };
  $("stopCamera").onclick = () => camera.stop();
  $("manualEntry").onclick = () => {
    $("ticketCode").focus();
    $("scanForm").scrollIntoView({ block: "nearest" });
  };
  $("scanForm").onsubmit = (event) => {
    event.preventDefault();
    unlockAudio();
    verify({ code: $("ticketCode").value.trim() }, "/api/scan");
  };
  $("nextTicket").onclick = () => {
    if (!verifying) {
      camera.release();
      ready();
      $("scannerCard").scrollIntoView({ block: "start" });
    }
  };
  $("refreshRecords").onclick = loadRecords;
  $("sampleCollection").onchange = loadSamples;
  $("signout").onclick = signout;
  $("adminSignout").onclick = signout;
  $("resetData").onclick = () => {
    if (appMode === "test") $("resetDialog").showModal();
  };
  $("resetForm").addEventListener("submit", (event) => {
    if (event.submitter?.value === "reset") {
      event.preventDefault();
      resetData();
    }
  });
  $("printSamples").onclick = async () => {
    if (appMode === "production") return;
    await Promise.all(
      [...document.querySelectorAll(".sample-qr")].map((image) =>
        image.decode().catch(() => {}),
      ),
    );
    window.print();
  };
  ["soundToggle", "vibrationToggle"].forEach(
    (id) =>
      ($(id).onchange = () => {
        unlockAudio();
        try {
          localStorage.setItem(
            "onrf2.feedback",
            JSON.stringify({
              sound: $("soundToggle").checked,
              vibration: $("vibrationToggle").checked,
            }),
          );
        } catch (_) {}
      }),
  );
  $("gate").onchange = () => {
    try {
      localStorage.setItem("onrf2.gate", $("gate").value);
    } catch (_) {}
  };
  window.addEventListener("pagehide", () => camera.stop());
  document.addEventListener("visibilitychange", () => {
    if (
      !document.hidden &&
      camera.running &&
      camera.stream
        ?.getVideoTracks()
        .every((track) => track.readyState === "ended")
    )
      camera.fail();
  });
  readPreferences();
  api("/api/me")
    .then(signedIn)
    .catch(() => {});
})();
