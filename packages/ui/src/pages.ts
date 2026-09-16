/** Cursor query data (InfiniteData or undefined) → flat rows; [] while pending. */
export function flattenPages<T>(data: { pages: { items: T[] }[] } | undefined): T[] {
  return (data?.pages ?? []).flatMap((p) => p.items);
}
