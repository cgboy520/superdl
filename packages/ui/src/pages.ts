/** 游标分页查询数据(InfiniteData 或 undefined)→ 平铺行;未就绪为 []。 */
export function flattenPages<T>(data: { pages: { items: T[] }[] } | undefined): T[] {
  return (data?.pages ?? []).flatMap((p) => p.items);
}
