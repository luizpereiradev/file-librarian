// The status page: reads /api/v1/status and shows its counts.
// Text goes in with textContent only, never as HTML; `backticks` become <code>.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);

  // The words, in the page's language, and how numbers and dates are shown: from the
  // server's catalogue (file_librarian.i18n), in a <meta> element.
  const BUNDLE = JSON.parse(document.querySelector("meta[name=flib-messages]").content || "{}");
  const FORMATS = BUNDLE.formats || { one: [1], decimal: ".", dateOrder: "ymd" };
  const MESSAGES = BUNDLE.m || {};

  // A message with its placeholders filled; a plural one picks its form from `by`.
  function tr(id, params) {
    let text = MESSAGES[id];
    if (text === undefined) return id;
    params = params || {};
    if (typeof text !== "string") {
      text = FORMATS.one.includes(params[text.by]) ? text.one : text.other;
    }
    return text.replace(/\{([a-z][a-z0-9_]*)\}/g, (_, key) => String(params[key]));
  }

  // Money and dates as every page shows them: US$ 0.0123, 2026-11-25 22:48 (pt-BR:
  // US$ 0,0123, 25/11/2026 22:48).
  function usd(value, places) {
    return "US$ " + value.toFixed(places).replace(".", FORMATS.decimal);
  }

  function money(value) {
    if (value === null || value === undefined) return tr("common.no_cost_recorded");
    return value === 0 ? tr("common.no_cost") : usd(value, 4);
  }

  function two(n) {
    return String(n).padStart(2, "0");
  }

  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const time = " " + two(d.getHours()) + ":" + two(d.getMinutes());
    if (FORMATS.dateOrder === "dmy") {
      return two(d.getDate()) + "/" + two(d.getMonth() + 1) + "/" + d.getFullYear() + time;
    }
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) + time;
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

  const TRIGGERS = ["schedule", "manual", "mcp", "watch", "reprocess"];
  const REASONS = ["low_confidence", "unknown_category", "category_removed", "moved_by_hand"];

  // "Not sure: Finance 2, Health 1": one line per reason, not one per category.
  function reviewLines(groups) {
    const byReason = new Map();
    for (const g of groups) {
      const label = tr("review.reason." + (REASONS.includes(g.reason) ? g.reason : "none"));
      if (!byReason.has(label)) byReason.set(label, []);
      byReason.get(label).push(g);
    }
    const lines = [];
    for (const [label, items] of byReason) {
      const named = items.filter((g) => g.suggested);
      const unnamed = items.filter((g) => !g.suggested).reduce((n, g) => n + g.count, 0);
      const parts = named.map((g) => tr("js.suggested_count", { suggested: g.suggested, count: g.count }));
      if (unnamed) parts.push(named.length ? tr("js.no_suggestion", { count: unnamed }) : String(unnamed));
      lines.push(tr("js.reason_line", { reason: label, parts: parts.join(", ") }));
    }
    return lines;
  }

  function show(data) {
    if (!data.database_exists) {
      say($("summary"), tr("common.no_index"));
    } else {
      const waiting = data.review.total + data.failed.total;
      $("summary").textContent = waiting
        ? tr("js.waiting", { count: waiting })
        : tr("js.nothing_waits");
    }

    $("review-total").textContent = data.review.total;
    const oldest = data.review.oldest_days;
    $("review-note").textContent =
      oldest === null ? tr("js.nothing_to_review") : oldest === 0 ? tr("js.oldest_today") :
      tr("js.oldest_days", { count: oldest });
    fillList($("review-groups"), reviewLines(data.review.groups));
    $("review-go").hidden = data.review.total === 0;

    $("failed-total").textContent = data.failed.total;
    fillList(
      $("failed-groups"),
      data.failed.groups.map((g) =>
        tr(g.retried ? "js.failed_retried" : "js.failed_given_up", { count: g.count, error: g.error })
      )
    );

    if (data.due) {
      $("due-total").textContent = data.due.upcoming + data.due.overdue;
      $("due-note").textContent = tr("js.due_note", {
        overdue: data.due.overdue,
        upcoming: data.due.upcoming,
        days: tr("js.days", { count: data.due.within_days }),
      });
    } else {
      $("due-total").textContent = "–";
      $("due-note").textContent = tr("js.due_after_run");
    }

    $("sensitive").textContent = data.sensitive;
    $("sensitive-note").textContent = tr("js.sensitive_note", { count: data.sensitive });
    $("duplicates").textContent = data.duplicates;

    const run = data.last_run;
    $("last-run").textContent = run
      ? tr("js.last_run", {
          when: when(run.finished_at),
          trigger: TRIGGERS.includes(run.trigger) ? tr("js.trigger." + run.trigger) : run.trigger,
          organized: tr("activity.organized", { count: run.organized }),
          review: tr("activity.to_review", { count: run.review }),
          failed: tr("activity.failed", { count: run.failed }),
          cost: money(run.cost_usd),
        })
      : tr("js.none_yet");

    const s = data.schedule;
    if (!s.installed) {
      say($("next-run"), tr("js.not_scheduled"));
    } else if (s.mode === "watch") {
      $("next-run").textContent = s.loaded ? tr("js.watching") : tr("js.watch_not_loaded");
    } else if (s.next_run) {
      $("next-run").textContent =
        s.mode === "both" ? tr("js.next_and_watch", { when: when(s.next_run) }) : when(s.next_run);
    } else {
      say($("next-run"), tr("js.not_loaded"));
    }

    const t = data.today;
    const cost = money(t.automatic_cost_usd);
    $("today").textContent =
      t.cap_usd === null
        ? tr("js.today", { cost })
        : tr(t.cap_reached ? "js.today_cap_reached" : "js.today_cap", { cost, cap: usd(t.cap_usd, 2) });
    $("month").textContent = data.month.runs_without_cost
      ? tr("js.month_unpriced", {
          cost: money(data.month.cost_usd),
          runs: tr("js.runs", { count: data.month.runs_without_cost }),
        })
      : money(data.month.cost_usd);

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
      say(summary, tr("js.unreadable", { error: error.message }));
      summary.classList.add("error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh").addEventListener("click", load);
    load();
  });
})();
