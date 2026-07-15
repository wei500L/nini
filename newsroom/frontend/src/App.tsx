import { lazy, Suspense } from "react";

import { LaunchPage } from "./components/LaunchPage";
import { StudioPage } from "./components/StudioPage";

const ReviewPage = lazy(() =>
  import("./components/ReviewPage").then((module) => ({
    default: module.ReviewPage,
  })),
);

export function App() {
  const reviewMatch = window.location.pathname.match(/^\/review\/([^/]+)\/?$/);
  if (reviewMatch) return (
    <Suspense fallback={<div className="route-loading">正在装载复盘台…</div>}>
      <ReviewPage reviewId={decodeURIComponent(reviewMatch[1])} />
    </Suspense>
  );
  if (window.location.pathname.match(/^\/studio\/?$/)) return <StudioPage />;
  return <LaunchPage />;
}
