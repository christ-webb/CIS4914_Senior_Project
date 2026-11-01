import { Suspense } from "react";

import { ErrorsView } from "@/components/errors/errors-view";

export default function ErrorsPage() {
  return (
    <Suspense fallback={null}>
      <ErrorsView />
    </Suspense>
  );
}
