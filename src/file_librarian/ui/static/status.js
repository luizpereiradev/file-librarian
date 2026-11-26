// The status page: reads /api/v1/status and shows its counts.
// Text goes in with textContent only, never as HTML.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);

  function files(n) {
    return n === 1 ? "1 file" : n + " files";
  }

  function money(value) {
    return value === null || value === undefined ? "no cost recorded" : "US$ " + value.toFixed(4);
  }

  function when(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    return isNaN(date) ? iso : date.toLocaleString();
  }

  function fillList(list, items) {
    list.replaceChildren();
    for (const text of items) {
      const li = document.createElement("li");
      li.textContent = text;
      list.append(li);
    }
  }

  const TRIGGERS = {
    schedule: "scheduled",
    manual: "by hand",
    mcp: "from an assistant",
    watch: "on arrival",
  };

  function show(data) {
    if (!data.database_exists) {
      $("summary").textContent = "No run yet: the index is created by the first `flib run`.";
    } else {
      const waiting = data.review.total + data.failed.total;
      $("summary").textContent = waiting
        ? files(waiting) + " wait for you."
        : "Nothing waits for you.";
    }

    $("review-total").textContent = data.review.total;
    $("review-note").textContent =
      data.review.oldest_days === null ? "nothing to review" : "the oldest for " + data.review.oldest_days + " day(s)";
    fillList($("review-groups"), data.review.groups.map((g) => g.count + ": " + g.explanation));

    $("failed-total").textContent = data.failed.total;
    fillList(
      $("failed-groups"),
      data.failed.groups.map((g) => g.count + ": " + g.error + (g.retried ? " (tried again next run)" : " (given up)"))
    );

    if (data.due) {
      $("due-total").textContent = data.due.upcoming + data.due.overdue;
      $("due-note").textContent =
        data.due.overdue + " overdue, " + data.due.upcoming + " in the next " + data.due.within_days + " days";
    } else {
      $("due-total").textContent = "–";
      $("due-note").textContent = "after the next run";
    }

    $("sensitive").textContent = data.sensitive;
    $("duplicates").textContent = data.duplicates;

    const run = data.last_run;
    $("last-run").textContent = run
      ? when(run.finished_at) + ", " + (TRIGGERS[run.trigger] || run.trigger) + ": " + run.organized +
        " organized, " + run.review + " to review, " + run.failed + " failed; " + money(run.cost_usd)
      : "none yet";

    const s = data.schedule;
    if (!s.installed) {
      $("next-run").textContent = "none scheduled (`flib schedule` sets one up)";
    } else if (s.mode === "watch") {
      $("next-run").textContent = s.loaded ? "as files arrive (watch mode)" : "watch mode installed but not loaded";
    } else {
      $("next-run").textContent = s.next_run
        ? when(s.next_run) + (s.mode === "both" ? ", and as files arrive" : "")
        : "installed but not loaded (run `flib schedule` again)";
    }

    const t = data.today;
    $("today").textContent =
      "automatic runs " + money(t.automatic_cost_usd) +
      (t.cap_usd === null ? "" : " of US$ " + t.cap_usd.toFixed(2)) +
      (t.cap_reached ? " (cap reached)" : "");
    $("month").textContent =
      money(data.month.cost_usd) +
      (data.month.runs_without_cost ? " (plus " + data.month.runs_without_cost + " run(s) without a cost)" : "");

    $("cards").hidden = false;
  }

  async function load() {
    const summary = $("summary");
    summary.classList.remove("error");
    try {
      const response = await fetch("/api/v1/status", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.status === 401) {
        window.location.reload();
        return;
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      show(data);
    } catch (error) {
      summary.textContent = "Could not read the status: " + error.message;
      summary.classList.add("error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh").addEventListener("click", load);
    load();
  });
})();
