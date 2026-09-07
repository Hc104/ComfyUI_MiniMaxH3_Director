/**
 * P0-B（#482）：构建标识（Build ID）。
 *
 * 值由 vite.config.ts 的 define 在「构建 / dev server 启动」时注入 `__BUILD_ID__`
 * （格式 `v{pkg.version}·b{yymmdd}-{hhmm}`，每次构建唯一）。本模块只做一次透出，
 * 不参与任何逻辑；顶栏右侧小字展示用，报 bug 时能精确知道用户跑的是哪次构建。
 *
 * ⛔ 纯函数、零副作用；测试环境（vitest）同样会收到 define 注入值。
 */
export const BUILD_ID: string = __BUILD_ID__;
