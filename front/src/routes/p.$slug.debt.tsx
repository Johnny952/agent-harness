import { createFileRoute } from "@tanstack/react-router";
import { ProjectGate } from "@/components/console/project-scope";
import { DebtPage } from "./debt";

/** One project's debt: the harness's Debt index over the index in that project's clone. ADR 210. */
export const Route = createFileRoute("/p/$slug/debt")({
  head: ({ params }) => ({
    meta: [
      { title: `${params.slug} debt index — harness operations console` },
      {
        name: "description",
        content: `What the harness knowingly left unfinished in project ${params.slug}.`,
      },
    ],
  }),
  component: ProjectDebt,
});

function ProjectDebt() {
  const { slug } = Route.useParams();
  return (
    <ProjectGate slug={slug}>
      <DebtPage />
    </ProjectGate>
  );
}
