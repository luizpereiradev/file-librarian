// The status page: reads /api/v1/status and shows its counts.
// Text goes in with textContent only, never as HTML; `backticks` become <code>.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);

  function plural(n, word, many) {
    return n === 1 ? "1 " + word : n + " " + (many || word + "s");
  }

  // Every page shows money and dates the same way: US$ 0.0123, 2026-11-25 22:48.
  function money(value) {
    if (value === null || value === undefined) return "no cost recorded";
    return value === 0 ? "no cost" : "US$ " + value.toFixed(4);
  }

  function two(n) {
    return String(n).padStart(2, "0");
  }

  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return (
      d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) +
      " " + two(d.getHours()) + ":" + two(d.getMinutes())
    );
  }

  // Puts text in an element; parts between backticks go in <code>.
  function say(element, text) {
    element.replaceChildren();
    String(text).split("`").forEach((part, i) => {
      if (!part) return;
      if (i % 2) {
        const code = document.createElement("code");
        code.textContent = part;
        element.append(code);
      } else {
        element.append(document.createTextNode(part));
      }
    });
  }

  function fillList(list, items) {
    list.replaceChildren();
    for (const text of items) {
      const li = document.createElement("li");
      say(li, text);
      list.append(li);
    }
  }

  const TRIGGERS = {
    schedule: "daily run",
    manual: "by hand",
    mcp: "from an assistant",
    watch: "on arrival",
    reprocess: "reprocess",
  };

  const REASONS = {
    low_confidence: "Not sure",
    unknown_category: "Category not configured",
    category_removed: "Category removed",
    moved_by_hand: "Moved there by hand",
  };

  // "Not sure: Finance 2, Health 1": one line per reason, not one per category.
  function reviewLines(groups) {
    const byReason = new Map();
    for (const g of groups) {
      const label = REASONS[g.reason] || "Filed before flib kept a reason";
      if (!byReason.has(label)) byReason.set(label, []);
      byReason.get(label).push(g);
    }
    const lines = [];
    for (const [label, items] of byReason) {
      const named = items.filter((g) => g.suggested);
      const unnamed = items.filter((g) => !g.suggested).reduce((n, g) => n + g.count, 0);
      const parts = named.map((g) => g.suggested + " " + g.count);
      if (unnamed) parts.push((named.length ? "no suggestion " : "") + unnamed);
      lines.push(label + ": " + parts.join(", "));
    }
    return lines;
  }

  function show(data) {
    if (!data.database_exists) {
      say($("summary"), "No run yet: the index is created by the first `flib run`.");
    } else {
      const waiting = data.review.total + data.failed.total;
      $("summary").textContent = waiting
        ? plural(waiting, "file") + (waiting === 1 ? " waits" : " wait") + " for you."
        : "Nothing waits for you.";
    }

    $("review-total").textContent = data.review.total;
    const oldest = data.review.oldest_days;
    $("review-note").textContent =
      oldest === null ? "Nothing to review." : oldest === 0 ? "The oldest since today." :
      "The oldest for " + plural(oldest, "day") + ".";
    fillList($("review-groups"), reviewLines(data.review.groups));
    $("review-go").hidden = data.review.total === 0;

    $("failed-total").textContent = data.failed.total;
    fillList(
      $("failed-groups"),
      data.failed.groups.map((g) => g.count + ": " + g.error + (g.retried ? " (tried again next run)" : " (given up)"))
    );

    if (data.due) {
      $("due-total").textContent = data.due.upcoming + data.due.overdue;
      $("due-note").textContent =
        data.due.overdue + " overdue, " + data.due.upcoming + " in the next " + plural(data.due.within_days, "day") + ".";
    } else {
      $("due-total").textContent = "–";
      $("due-note").textContent = "After the next run.";
    }

    $("sensitive").textContent = data.sensitive;
    $("sensitive-note").textContent =
      (data.sensitive === 1 ? "Sensitive file" : "Sensitive files") + ", never moved or sent anywhere.";
    $("duplicates").textContent = data.duplicates;

    const run = data.last_run;
    $("last-run").textContent = run
      ? when(run.finished_at) + ", " + (TRIGGERS[run.trigger] || run.trigger) + ": " + run.organized +
        " organized, " + run.review + " to review, " + run.failed + " failed; " + money(run.cost_usd)
      : "None yet";

    const s = data.schedule;
    if (!s.installed) {
      say($("next-run"), "None scheduled (`flib schedule` sets one up)");
    } else if (s.mode === "watch") {
      $("next-run").textContent = s.loaded ? "As files arrive (watch mode)" : "Watch mode installed but not loaded";
    } else if (s.next_run) {
      $("next-run").textContent = when(s.next_run) + (s.mode === "both" ? ", and as files arrive" : "");
    } else {
      say($("next-run"), "Installed but not loaded (run `flib schedule` again)");
    }

    const t = data.today;
    $("today").textContent =
      "Automatic runs: " + money(t.automatic_cost_usd) +
      (t.cap_usd === null ? "" : " (daily cap US$ " + t.cap_usd.toFixed(2) + ")") +
      (t.cap_reached ? ", cap reached" : "");
    $("month").textContent =
      money(data.month.cost_usd) +
      (data.month.runs_without_cost ? " (plus " + plural(data.month.runs_without_cost, "run") + " without a cost)" : "");

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
      say(summary, "Could not read the status: " + error.message);
      summary.classList.add("error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh").addEventListener("click", load);
    load();
  });
})();
