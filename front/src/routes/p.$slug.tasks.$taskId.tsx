import { createFileRoute } from "@tanstack/react-router";
import { ProjectGate, ProjectTaskGate } from "@/components/console/project-scope";
import { TaskDetailPage } from "./tasks.$taskId";

/** One task's page inside its project: the harness's task page, for a task of that project. ADR NNN. */
export const Route = createFileRoute("/p/$slug/tasks/$taskId")({
  head: ({ params }) => ({
    meta: [{ title: `${params.taskId} — ${params.slug} — task detail` }],
  }),
  component: ProjectTask,
});

function ProjectTask() {
  const { slug, taskId } = Route.useParams();
  return (
    <ProjectGate slug={slug}>
      <ProjectTaskGate slug={slug} taskId={taskId}>
        <TaskDetailPage taskId={taskId} />
      </ProjectTaskGate>
    </ProjectGate>
  );
}
