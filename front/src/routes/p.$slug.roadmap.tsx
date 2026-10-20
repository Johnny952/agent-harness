import { createFileRoute } from "@tanstack/react-router";
import { ProjectGate } from "@/components/console/project-scope";
import { RoadmapPage, Route as HarnessRoadmapRoute } from "./roadmap";

/** One project's roadmap: the harness's Roadmap over the plans in that project's clone. ADR 210. */
export const Route = createFileRoute("/p/$slug/roadmap")({
  // The harness's Roadmap keeps `?view=`; this route keeps it the same way.
  validateSearch: HarnessRoadmapRoute.options.validateSearch,
  head: ({ params }) => ({
    meta: [
      { title: `${params.slug} roadmap — harness operations console` },
      {
        name: "description",
        content: `Project ${params.slug}'s plans in three bands, future, now and past, with the tasks each names.`,
      },
    ],
  }),
  component: ProjectRoadmap,
});

function ProjectRoadmap() {
  const { slug } = Route.useParams();
  return (
    <ProjectGate slug={slug}>
      <RoadmapPage />
    </ProjectGate>
  );
}
