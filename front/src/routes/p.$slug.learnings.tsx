import { createFileRoute } from "@tanstack/react-router";
import { ProjectGate } from "@/components/console/project-scope";
import { LearningsPage } from "./learnings";

/** One project's learnings: the harness's inbox over the entries that apply to that project. ADR NNN. */
export const Route = createFileRoute("/p/$slug/learnings")({
  head: ({ params }) => ({
    meta: [
      { title: `${params.slug} learnings — harness operations console` },
      {
        name: "description",
        content: `The learnings a phase of project ${params.slug} can be handed, and how sure each is.`,
      },
    ],
  }),
  component: ProjectLearnings,
});

function ProjectLearnings() {
  const { slug } = Route.useParams();
  return (
    <ProjectGate slug={slug}>
      <LearningsPage />
    </ProjectGate>
  );
}
