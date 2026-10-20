import { createFileRoute } from "@tanstack/react-router";
import { ProjectGate } from "@/components/console/project-scope";
import { BacklogPage } from "./backlog";

/** One project's backlog: the harness's Backlog screen over that project's tasks. ADR NNN. */
export const Route = createFileRoute("/p/$slug/backlog")({
  head: ({ params }) => ({
    meta: [
      { title: `${params.slug} backlog — harness operations console` },
      {
        name: "description",
        content: `What waits to be accepted onto the board for project ${params.slug}.`,
      },
    ],
  }),
  component: ProjectBacklog,
});

function ProjectBacklog() {
  const { slug } = Route.useParams();
  return (
    <ProjectGate slug={slug}>
      <BacklogPage />
    </ProjectGate>
  );
}
