/// <reference types="vite/client" />

/** P0-B（#482）：构建时由 vite.config.ts define 注入的构建标识（每次构建唯一）。 */
declare const __BUILD_ID__: string;

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<object, object, unknown>;
  export default component;
}
