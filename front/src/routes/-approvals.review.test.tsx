/**
 * The Approvals screen's review (ADR 219), mounted whole through `renderRoute`
 * with what `/api/tasks`, `/api/tasks/<id>/diff` and `/api/tasks/<id>/comments`
 * served (`reviewLane` and `parseDiff` are `src/lib/review.test.ts`'s). What these hold: *Awaiting review* lists a `done` task not on main
 * and *Conflict* a `conflict` one, and no other; the diff is drawn by file with
 * its counts and the new side's line numbers; a pinned comment sits under its
 * line at the tip and is listed as outdated past it; a line number pins a new
 * comment to that line at the tip; the *comment* verdict (ADR 222) is listed
 * with its tip, marked past it, and posted at the tip, and these two posts are
 * the screen's only writes; a missing diff and a failed read each say so; and
 * approve is the CLI line, not a button.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { renderRoute, type Served } from "@/test/render-route";
import type { Task, TaskComment, TaskDiff } from "@/lib/api/types";
import { Route } from "./approvals";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(cleanup);

function task(id: string, over: Partial<Task> = {}): Task {
  return {
    task_id: id,
    status: "done",
    owner: null,
    depends_on: [],
    heartbeat: null,
    description: null,
    resolved_debt: [],
    lock_expired: null,
    phase: null,
    priority: "normal",
    source: "operator",
    tags: [],
    rank: null,
    debt_id: null,
    project: "ia-harness",
    epic: null,
    split_into: [],
    merged: false,
    title: `title of ${id}`,
    ...over,
  };
}

const TIP = "a".repeat(40);
const OLD = "b".repeat(40);

const DIFF_TEXT = [
  "diff --git a/src/app.py b/src/app.py",
  "index 1111111..2222222 100644",
  "--- a/src/app.py",
  "+++ b/src/app.py",
  "@@ -10,3 +10,4 @@ def main():",
  " keep = 1",
  "-gone = 2",
  "+added = 2",
  "+more = 3",
  " tail = 4",
  "diff --git a/README.md b/README.md",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/README.md",
  "@@ -0,0 +1 @@",
  "+hello",
  "",
].join("\n");

function diff(over: Partial<TaskDiff> = {}): TaskDiff {
  return {
    task_id: "T-1",
    project: "ia-harness",
    branch: "agent/task/T-1",
    base: "main",
    commit: TIP,
    merge_base: "c".repeat(40),
    diff: DIFF_TEXT,
    truncated: false,
    ...over,
  };
}

const AT_TIP: TaskComment = {
  at: "2026-10-19T10:00:00Z",
  author: "operator",
  text: "why two?",
  file: "src/app.py",
  line: 11,
  commit: TIP,
};
const PAST: TaskComment = {
  at: "2026-10-18T10:00:00Z",
  author: "operator",
  text: "rename this",
  file: "src/app.py",
  line: 3,
  commit: OLD,
};

const VERDICT_AT_TIP: TaskComment = {
  at: "2026-10-20T09:00:00Z",
  author: "operator",
  text: "reads well so far",
  verdict: { kind: "comment", commit: TIP },
};
const VERDICT_PAST: TaskComment = {
  at: "2026-10-17T09:00:00Z",
  author: "operator",
  text: "the first cut",
  verdict: { kind: "comment", commit: OLD },
};

function serve(tasks: Task[], extra: Served = {}) {
  return renderRoute(Route, "/approvals", {
    "/api/command-requests": { data: { requests: [], refused: [] }, warnings: [] },
    "/api/tasks": { data: tasks, warnings: [] },
    "/api/tasks/T-1/diff": { data: diff(), warnings: [] },
    "/api/tasks/T-1/comments": { data: { task_id: "T-1", comments: [] }, warnings: [] },
    ...extra,
  });
}

describe("the review", () => {
  it("lists done-not-merged under Awaiting review and conflicts apart, and nothing else", async () => {
    await serve([
      task("T-1"),
      task("T-2", { merged: true }),
      task("T-3", { status: "in_progress" }),
      task("T-4", { status: "conflict" }),
    ]);
    const tab = await screen.findByRole("button", { name: "Awaiting review · 1" });
    const rows = await screen.findAllByTestId("review-row");
    expect(rows.map((r) => within(r).getByText(/^T-/).textContent)).toEqual(["T-1"]);

    fireEvent.click(screen.getByRole("button", { name: "Conflict · 1" }));
    const conflict = await screen.findAllByTestId("review-row");
    expect(conflict.map((r) => within(r).getByText(/^T-/).textContent)).toEqual(["T-4"]);
    expect(tab).toBeTruthy();
  });

  it("draws the branch, each file with its counts, and the CLI merge line", async () => {
    await serve([task("T-1")]);
    const branch = await screen.findByTestId("review-branch");
    expect(branch.textContent).toContain("agent/task/T-1");
    expect(branch.textContent).toContain("main");
    expect(branch.textContent).toContain(TIP.slice(0, 12));

    const files = screen.getAllByTestId("diff-file");
    expect(files).toHaveLength(2);
    const first = files[0]!;
    expect(first.textContent).toContain("src/app.py");
    expect(first.textContent).toContain("+2");
    expect(first.textContent).toContain("−1");
    // The header lines are not drawn as the file's lines.
    expect(first.textContent).not.toContain("index 1111111");

    const verdict = screen.getByTestId("review-verdict");
    expect(within(verdict).getByText("dispatch merge-task T-1 --project ia-harness")).toBeTruthy();
    expect(verdict.textContent).toMatch(/not in the console yet/i);
  });

  it("puts a comment at the tip under its line, and one past it in the outdated list", async () => {
    await serve([task("T-1")], {
      "/api/tasks/T-1/comments": {
        data: { task_id: "T-1", comments: [AT_TIP, PAST] },
        warnings: [],
      },
    });
    const files = await screen.findAllByTestId("diff-file");
    expect(within(files[0]!).getByText("why two?")).toBeTruthy();
    expect(within(files[0]!).queryByText("rename this")).toBeNull();

    const outdated = screen.getByTestId("outdated-comments");
    expect(within(outdated).getByText("rename this")).toBeTruthy();
    expect(outdated.textContent).toContain(`src/app.py:3@${OLD}`);
    expect(within(outdated).queryByText("why two?")).toBeNull();
  });

  it("says a branch with no change of its own has none, and notes a cut diff", async () => {
    await serve([task("T-1")], {
      "/api/tasks/T-1/diff": { data: diff({ diff: "" }), warnings: [] },
    });
    expect(await screen.findByText("No change of its own")).toBeTruthy();
    cleanup();

    await serve([task("T-1")], {
      "/api/tasks/T-1/diff": { data: diff({ truncated: true }), warnings: ["over 200 bytes"] },
    });
    expect(await screen.findByText(/the diff is cut here/i)).toBeTruthy();
    expect(screen.getByText("over 200 bytes")).toBeTruthy();
  });

  it("shows the api's reason when there is no diff to draw", async () => {
    const why = "no branch agent/task/T-1: the task has not committed there";
    await serve([task("T-1")], { "/api/tasks/T-1/diff": { data: null, warnings: [why] } });
    expect(await screen.findByText(why)).toBeTruthy();
    expect(screen.getByText(/there is no diff to show/i)).toBeTruthy();
    expect(screen.queryByTestId("diff-file")).toBeNull();
  });

  it("names a conflict's way out: the base merged into the branch, then the merge again", async () => {
    await serve([task("T-1", { status: "conflict" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Conflict · 1" }));
    const verdict = await screen.findByTestId("review-verdict");
    expect(verdict.textContent).toMatch(/merge the base into the task branch/i);
    expect(within(verdict).getByText("dispatch merge-task T-1 --project ia-harness")).toBeTruthy();
  });

  it("shows the error, not an empty list, when the board or the diff does not answer", async () => {
    await serve([], { "/api/tasks": { status: 502, error: "board down" } });
    expect(await screen.findByText("The board could not be read")).toBeTruthy();
    expect(screen.getByText(/board down/)).toBeTruthy();
    expect(screen.queryByText("Nothing is awaiting review")).toBeNull();
    cleanup();

    await serve([task("T-1")], { "/api/tasks/T-1/diff": { status: 502, error: "git hung" } });
    expect(await screen.findByText("The diff could not be read")).toBeTruthy();
    expect(screen.getByText(/git hung/)).toBeTruthy();
  });

  it("says so when nothing waits, in each list", async () => {
    await serve([task("T-2", { merged: true })]);
    expect(await screen.findByText("Nothing is awaiting review")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Conflict · 0" }));
    expect(await screen.findByText("No task is in conflict")).toBeTruthy();
  });

  it("pins a comment to a new-side line at the tip through the board-service", async () => {
    const posted = { ...AT_TIP, text: "check this", line: 12 };
    const { sent } = await serve([task("T-1")], {
      "POST /api/tasks/T-1/comments": {
        data: { id: "T-1", comment: posted, comment_count: 1 },
      },
    });
    const files = await screen.findAllByTestId("diff-file");
    // A removed line has no new-side number, so nothing to pin to.
    const numbers = within(files[0]!)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(numbers).toEqual(["10", "11", "12", "13"]);

    fireEvent.click(screen.getByRole("button", { name: "comment on src/app.py line 12" }));
    const form = screen.getByTestId("line-comment-form");
    const pin = within(form).getByRole("button", { name: "pin comment" });
    expect((pin as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText("comment on src/app.py line 12"), {
      target: { value: "check this" },
    });
    fireEvent.click(pin);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("comment added to src/app.py:12"),
    );
    expect(sent.filter((x) => x.method !== "GET")).toEqual([
      {
        method: "POST",
        path: "/api/tasks/T-1/comments",
        body: { text: "check this", file: "src/app.py", line: 12, commit: TIP },
      },
    ]);
    await waitFor(() => expect(screen.queryByTestId("line-comment-form")).toBeNull());
  });

  it("shows a refused pin in a toast and keeps the form", async () => {
    await serve([task("T-1")], {
      "POST /api/tasks/T-1/comments": { status: 400, error: "the file is not in the repository" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "comment on README.md line 1" }));
    fireEvent.change(screen.getByRole("textbox", { name: "comment on README.md line 1" }), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByRole("button", { name: "pin comment" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("the file is not in the repository"),
      ),
    );
    expect(screen.getByTestId("line-comment-form")).toBeTruthy();
  });

  it("lists the verdicts given, with their tip, and marks one past it", async () => {
    await serve([task("T-1")], {
      "/api/tasks/T-1/comments": {
        data: { task_id: "T-1", comments: [AT_TIP, VERDICT_PAST, VERDICT_AT_TIP] },
        warnings: [],
      },
    });
    const box = await screen.findByTestId("review-verdicts");
    expect(box.textContent).toContain("verdicts · 2");
    const given = within(box).getAllByTestId("review-comment");
    expect(given.map((g) => g.querySelector("p")?.textContent)).toEqual([
      "the first cut",
      "reads well so far",
    ]);
    expect(given[0]!.textContent).toContain(`comment at ${OLD.slice(0, 12)} · at an earlier tip`);
    expect(given[1]!.textContent).toContain(`comment at ${TIP.slice(0, 12)}`);
    expect(given[1]!.textContent).not.toContain("earlier tip");
    // A verdict has no anchor: it is neither under a line nor outdated.
    expect(screen.queryByTestId("outdated-comments")).toBeNull();
    const files = screen.getAllByTestId("diff-file");
    expect(within(files[0]!).queryByText("reads well so far")).toBeNull();
  });

  it("posts the comment verdict at the tip through the board-service", async () => {
    const posted = { ...VERDICT_AT_TIP, text: "fine by me" };
    const { sent } = await serve([task("T-1")], {
      "POST /api/tasks/T-1/verdict": {
        data: { id: "T-1", comment: posted, comment_count: 1 },
      },
    });
    const form = await screen.findByTestId("verdict-form");
    const button = within(form).getByRole("button", { name: "comment verdict" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(form).getByLabelText("comment verdict on T-1"), {
      target: { value: "fine by me" },
    });
    fireEvent.click(button);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("comment verdict recorded on T-1"),
    );
    expect(sent.filter((x) => x.method !== "GET")).toEqual([
      {
        method: "POST",
        path: "/api/tasks/T-1/verdict",
        body: { kind: "comment", text: "fine by me", commit: TIP },
      },
    ]);
    await waitFor(() =>
      expect((within(form).getByRole("textbox") as HTMLTextAreaElement).value).toBe(""),
    );
  });

  it("shows a refused verdict in a toast and keeps what was typed", async () => {
    await serve([task("T-1")], {
      "POST /api/tasks/T-1/verdict": {
        status: 400,
        error: "it is merged, not under review",
      },
    });
    const box = await screen.findByLabelText("comment verdict on T-1");
    fireEvent.change(box, { target: { value: "late" } });
    fireEvent.click(screen.getByRole("button", { name: "comment verdict" }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("it is merged, not under review"),
      ),
    );
    expect((box as HTMLTextAreaElement).value).toBe("late");
  });

  it("reads only its routes, and writes nothing until a pin or a verdict is posted", async () => {
    const { fetchMock } = await serve([task("T-1")]);
    await screen.findAllByTestId("diff-file");
    const paths = fetchMock.mock.calls.map((c) => new URL(String(c[0]), "http://x").pathname);
    expect(paths).toContain("/api/tasks/T-1/diff");
    expect(paths).toContain("/api/tasks/T-1/comments");
    // `/api/actions` is the sidebar's waiting count (ADR 217).
    const known = [
      "/api/tasks",
      "/api/projects",
      "/api/actions",
      "/api/command-requests",
      "/api/tasks/T-1/diff",
      "/api/tasks/T-1/comments",
    ];
    expect(paths.every((p) => known.includes(p))).toBe(true);
    for (const call of fetchMock.mock.calls) {
      expect((call[1]?.method ?? "GET").toUpperCase()).toBe("GET");
    }
    // No approve button: the review's buttons are the tabs, the rows, the
    // diff's line numbers and the comment verdict.
    const review = screen.getByRole("region", { name: "Review" });
    const names = within(review)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent ?? "");
    expect(
      names.every((n) =>
        /^(Awaiting review|Conflict) · \d+$|^T-1|^comment on \S+ line \d+$|^comment verdict$/.test(
          n,
        ),
      ),
    ).toBe(true);
  });
});
