/**
 * The Approvals screen's review (ADR NNN), mounted whole through `renderRoute`
 * with what `/api/tasks`, `/api/tasks/<id>/diff` and `/api/tasks/<id>/comments`
 * served (`reviewLane` and `parseDiff` are `src/lib/review.test.ts`'s). What these hold: *Awaiting review* lists a `done` task not on main
 * and *Conflict* a `conflict` one, and no other; the diff is drawn by file with
 * its counts and the new side's line numbers; a pinned comment sits under its
 * line at the tip and is listed as outdated past it; a line number pins a new
 * comment to that line at the tip, and that post is the screen's one write; a
 * missing diff and a failed read each say so; and the verdict is the CLI line,
 * not a button.
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

  it("reads only its routes, and writes nothing until a pin is posted", async () => {
    const { fetchMock } = await serve([task("T-1")]);
    await screen.findAllByTestId("diff-file");
    const paths = fetchMock.mock.calls.map((c) => new URL(String(c[0]), "http://x").pathname);
    expect(paths).toContain("/api/tasks/T-1/diff");
    expect(paths).toContain("/api/tasks/T-1/comments");
    const known = [
      "/api/tasks",
      "/api/projects",
      "/api/command-requests",
      "/api/tasks/T-1/diff",
      "/api/tasks/T-1/comments",
    ];
    expect(paths.every((p) => known.includes(p))).toBe(true);
    for (const call of fetchMock.mock.calls) {
      expect((call[1]?.method ?? "GET").toUpperCase()).toBe("GET");
    }
    // No verdict button: the review's buttons are the tabs, the rows and the
    // diff's line numbers.
    const review = screen.getByRole("region", { name: "Review" });
    const names = within(review)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent ?? "");
    expect(
      names.every((n) =>
        /^(Awaiting review|Conflict) · \d+$|^T-1|^comment on \S+ line \d+$/.test(n),
      ),
    ).toBe(true);
  });
});
