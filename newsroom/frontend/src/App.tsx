import { lazy, Suspense } from "react";

import { StudioPage } from "./components/StudioPage";

const ReviewPage = lazy(() =>
  import("./components/ReviewPage").then((module) => ({
    default: module.ReviewPage,
  })),
);

export function App() {
  const reviewMatch = window.location.pathname.match(/^\/review\/([^/]+)\/?$/);
  return reviewMatch ? (
    <Suspense fallback={<div className="route-loading">正在装载复盘台…</div>}>
      <ReviewPage reviewId={decodeURIComponent(reviewMatch[1])} />
    </Suspense>
  ) : (
    <StudioPage />
  );
}
