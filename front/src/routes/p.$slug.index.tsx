import { createFileRoute } from "@tanstack/react-router";
import { ProjectGate } from "@/components/console/project-scope";
import { BoardPage } from "./index";

/** One project's board: the harness's Board screen over that project's tasks. ADR NNN. */
export const Route = createFileRoute("/p/$slug/")({
  head: ({ params }) => ({
    meta: [
      { title: `${params.slug} board — harness operations console` },
      {
        name: "description",
        content: `The accepted tasks of project ${params.slug}, by where they are.`,
      },
    ],
  }),
  component: ProjectBoard,
});

function ProjectBoard() {
  const { slug } = Route.useParams();
  return (
    <ProjectGate slug={slug}>
      <BoardPage />
    </ProjectGate>
  );
}
