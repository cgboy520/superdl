/** 落地页 Hero 与登录页品牌区共用的网格纹理(data URI SVG,白 7% 描边);
 *  叠在 brand.heroBg 渐变之上(渐变即 background-image)。 */
export const GRID_TEXTURE = `url("data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'><path d='M40 0H0v40' fill='none' stroke='rgba(255,255,255,0.07)'/></svg>`,
)}")`;
