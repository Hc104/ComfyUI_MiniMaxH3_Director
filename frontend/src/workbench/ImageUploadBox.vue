<script setup lang="ts">
/**
 * ImageUploadBox — 参考素材上传/预览组件（V1.2.5 图片 / V1.2.6 视频+音频）。
 *
 * 能力：点击上传、拖拽上传、缩略图/播放预览、上传进度条、替换/删除、失败提示。
 * mediaType 决定端点（comfyApi.uploadFile kind=image|video|audio）、accept、大小上限与预览形态。
 * 重复文件处理：ComfyUI /upload overwrite=false → 同名自动加 _1/_2 后缀，不覆盖旧文件。
 * compact 模式：资产 chip 用的小尺寸（预览 + 替换/删除/上传 +）。
 *
 * 数据流：选择文件 → api.uploadFile → {name, subfolder} → relPath = subfolder/name
 *   → emit update:modelValue(relPath)（父组件写回 Asset.imageFile / Scene.referenceImage / Shot refs）。
 * 预览：/view?filename=…&subfolder=…&type=input（ComfyUI input 目录，图片/视频/音频通用）。
 */
import { computed, ref } from "vue";
import { comfyInputUrl, uploadedRelPath, type UploadMediaOptions, type UploadedMedia } from "@/services/comfyApi";

export type MediaType = "image" | "video" | "audio";

/** ImageUploadBox 只依赖上传这一个能力（便于测试 mock，也避免绑定 ComfyApiClient 类结构）。 */
export interface MediaUploadApi {
  uploadFile(file: File, opts?: UploadMediaOptions): Promise<UploadedMedia>;
}

const MEDIA_LABEL: Record<MediaType, string> = { image: "图片", video: "视频", audio: "音频" };
const MEDIA_MAX_MB: Record<MediaType, number> = { image: 20, video: 500, audio: 100 };
const MEDIA_ACCEPT: Record<MediaType, string> = {
  image: "image/*",
  video: "video/*,.mp4,.webm,.mov,.avi,.mkv",
  audio: "audio/*,.wav,.mp3,.ogg,.flac,.m4a,.aac,.aiff",
};

const props = withDefaults(
  defineProps<{
    /** 当前 input 相对路径（空 = 无素材）。 */
    modelValue: string;
    /** 上传目标：input 目录内子目录。 */
    subfolder?: string;
    /** 上传客户端（至少提供 uploadFile）。 */
    api: MediaUploadApi;
    /** 素材类型：image/video/audio，决定端点/校验/预览。 */
    mediaType?: MediaType;
    /** 空态按钮文案（默认按类型）。 */
    placeholder?: string;
    /** 无素材时的 alt 提示。 */
    label?: string;
    /** 紧凑模式（资产 chip / ref 列表用）。 */
    compact?: boolean;
    disabled?: boolean;
  }>(),
  {
    subfolder: "minimax_studio/assets",
    placeholder: "",
    label: "",
    mediaType: "image",
    compact: false,
    disabled: false,
  },
);

const emit = defineEmits<{
  (e: "update:modelValue", relPath: string): void;
  (e: "uploaded", relPath: string): void;
  (e: "error", message: string): void;
}>();

const inputRef = ref<HTMLInputElement | null>(null);
const uploading = ref(false);
const progress = ref(0);
const errorMsg = ref("");
const dragging = ref(false);
const mediaBroken = ref(false);

const previewUrl = computed(() => (props.modelValue ? comfyInputUrl(props.modelValue) : ""));
const placeholderText = computed(() => props.placeholder || `上传${MEDIA_LABEL[props.mediaType]}`);
const acceptAttr = computed(() => MEDIA_ACCEPT[props.mediaType]);
const isImage = computed(() => props.mediaType === "image");
const isVideo = computed(() => props.mediaType === "video");
const isAudio = computed(() => props.mediaType === "audio");

function pick() {
  if (props.disabled || uploading.value) return;
  inputRef.value?.click();
}

function reset() {
  if (inputRef.value) inputRef.value.value = "";
}

function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) void startUpload(file);
  reset();
}

function onDrop(e: DragEvent) {
  dragging.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) void startUpload(file);
}

function acceptTest(file: File): boolean {
  const t = file.type;
  if (props.mediaType === "image") return t.startsWith("image/");
  if (props.mediaType === "video") {
    return t.startsWith("video/") || /\.(mp4|webm|mov|avi|mkv)$/i.test(file.name);
  }
  return t.startsWith("audio/") || /\.(wav|mp3|ogg|flac|m4a|aac|aiff)$/i.test(file.name);
}

function startUpload(file: File) {
  if (props.disabled || uploading.value) return;
  errorMsg.value = "";
  if (!acceptTest(file)) {
    errorMsg.value = `只支持${MEDIA_LABEL[props.mediaType]}文件`;
    return;
  }
  const maxBytes = MEDIA_MAX_MB[props.mediaType] * 1024 * 1024;
  if (file.size > maxBytes) {
    errorMsg.value = `${MEDIA_LABEL[props.mediaType]}超过 ${MEDIA_MAX_MB[props.mediaType]}MB`;
    return;
  }
  uploading.value = true;
  progress.value = 0;
  mediaBroken.value = false;
  props.api
    .uploadFile(file, {
      kind: props.mediaType,
      subfolder: props.subfolder,
      overwrite: false,
      onProgress: (loaded, total) => {
        progress.value = total > 0 ? Math.round((loaded / total) * 100) : 0;
      },
    })
    .then((u) => {
      const rel = uploadedRelPath(u);
      emit("update:modelValue", rel);
      emit("uploaded", rel);
    })
    .catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      errorMsg.value = msg;
      emit("error", msg);
    })
    .finally(() => {
      uploading.value = false;
    });
}

function remove() {
  if (props.disabled || uploading.value) return;
  emit("update:modelValue", "");
}
</script>

<template>
  <div
    class="img-upload"
    :class="{ compact, drop: dragging, disabled }"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
  >
    <!-- ─── compact：小预览 / ＋ 按钮 ─── -->
    <template v-if="compact">
      <template v-if="modelValue">
        <img
          v-if="isImage && !mediaBroken"
          class="c-thumb"
          :src="previewUrl"
          :alt="label || MEDIA_LABEL[mediaType]"
          :title="modelValue.split('/').pop()"
          @error="mediaBroken = true"
          @click="pick"
        />
        <span
          v-else-if="isImage && mediaBroken"
          class="c-thumb c-broken"
          :title="modelValue.split('/').pop()"
          @click="pick"
        >🖼</span>
        <span
          v-else
          class="c-thumb c-media"
          :title="modelValue.split('/').pop()"
          @click="pick"
        >{{ isVideo ? "🎬" : "🎵" }}</span>
        <button
          class="c-del"
          title="移除素材"
          :disabled="disabled || uploading"
          @click.stop="remove"
        >×</button>
      </template>
      <button
        v-else
        class="c-add"
        :title="uploading ? `上传中 ${progress}%` : placeholderText"
        :disabled="disabled || uploading"
        @click="pick"
      >{{ uploading ? `${progress}%` : "＋" }}</button>
    </template>

    <!-- ─── 常规：预览卡片 / 上传入口 ─── -->
    <template v-else>
      <template v-if="modelValue">
        <div class="img-thumb-wrap" @click="pick">
          <img
            v-if="isImage && !mediaBroken"
            class="img-thumb"
            :src="previewUrl"
            :alt="label || MEDIA_LABEL[mediaType]"
            @error="mediaBroken = true"
          />
          <div v-else-if="isImage && mediaBroken" class="img-thumb img-broken">🖼<br /><small>{{ modelValue.split("/").pop() }}</small></div>
          <video
            v-else-if="isVideo"
            class="img-thumb media-preview video-preview"
            :src="previewUrl"
            controls
            preload="metadata"
            @error="mediaBroken = true"
            @click.stop
          />
          <audio
            v-else-if="isAudio"
            class="media-preview audio-preview"
            :src="previewUrl"
            controls
            preload="metadata"
            @error="mediaBroken = true"
            @click.stop
          />
          <span class="img-file-name" :title="modelValue">{{ modelValue.split("/").pop() }}</span>
        </div>
        <div class="img-actions">
          <button class="img-btn" :disabled="disabled || uploading" @click="pick">替换</button>
          <button class="img-btn danger" :disabled="disabled || uploading" @click="remove">删除</button>
        </div>
      </template>
      <template v-else>
        <button class="img-empty" :disabled="disabled || uploading" @click="pick">
          <span class="img-plus">＋</span>
          <span>{{ uploading ? `上传中 ${progress}%` : placeholderText }}</span>
          <span class="img-hint">点击或拖拽{{ MEDIA_LABEL[mediaType] }}到此处</span>
        </button>
      </template>
      <div v-if="uploading" class="img-progress">
        <div class="img-progress-fill" :style="{ width: `${progress}%` }" />
        <span class="img-progress-txt">{{ progress }}%</span>
      </div>
    </template>

    <!-- 失败提示（共用） -->
    <div v-if="errorMsg" class="img-err">⚠ {{ errorMsg }}</div>

    <input
      ref="inputRef"
      type="file"
      :accept="acceptAttr"
      hidden
      @change="onFile"
    />
  </div>
</template>

<style scoped>
.img-upload {
  position: relative;
  border: 1px dashed #3a3a55;
  border-radius: 8px;
  padding: 6px;
  background: #161625;
  transition: border-color 0.15s, background 0.15s;
}
.img-upload.drop {
  border-color: #4a7dff;
  background: #1c2a4a;
}
.img-upload.disabled {
  opacity: 0.5;
}

/* compact 模式 */
.img-upload.compact {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: none;
  padding: 0;
  background: transparent;
}
.c-thumb {
  width: 26px;
  height: 26px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid #2a2a40;
  cursor: pointer;
  background: #0a0a10;
}
.c-thumb:hover {
  border-color: #4a7dff;
}
.c-thumb.c-broken {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #26263a;
  font-size: 12px;
}
.c-thumb.c-media {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #20203a;
  font-size: 13px;
}
.c-add {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  border: 1px dashed #3a3a55;
  background: #1a1a2a;
  color: #4a7dff;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
}
.c-add:hover:not(:disabled) {
  background: #1c2a4a;
  border-color: #4a7dff;
}
.c-add:disabled {
  opacity: 0.6;
  cursor: default;
}
.c-del {
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: #666;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 0;
}
.c-del:hover:not(:disabled) {
  color: #ff6b6b;
  background: rgba(255, 107, 107, 0.12);
}
.c-del:disabled {
  opacity: 0.5;
  cursor: default;
}

/* 常规模式 */
.img-thumb-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.img-thumb {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 6px;
  background: #0a0a10;
  border: 1px solid #2a2a40;
}
.img-thumb.img-broken {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  color: #666;
  text-align: center;
}
.img-thumb.img-broken small {
  font-size: 9px;
  max-width: 52px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.media-preview {
  border-radius: 6px;
  background: #0a0a10;
  border: 1px solid #2a2a40;
}
.media-preview.video-preview {
  width: 96px;
  height: 56px;
  object-fit: contain;
}
.media-preview.audio-preview {
  width: 160px;
  height: 32px;
}
.img-file-name {
  flex: 1;
  font-size: 12px;
  color: #9aa;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.img-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
.img-btn {
  flex: 1;
  background: #26263a;
  border: 1px solid #2a2a40;
  color: #c8c8d8;
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  cursor: pointer;
}
.img-btn:hover:not(:disabled) {
  background: #33334d;
}
.img-btn.danger {
  color: #ff9a9a;
}
.img-btn.danger:hover:not(:disabled) {
  background: rgba(255, 107, 107, 0.12);
}
.img-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.img-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 14px 8px;
  background: transparent;
  border: none;
  color: #8a8a9a;
  cursor: pointer;
  border-radius: 6px;
}
.img-empty:hover:not(:disabled) {
  color: #cfe0ff;
  background: rgba(74, 125, 255, 0.08);
}
.img-empty:disabled {
  opacity: 0.5;
  cursor: default;
}
.img-plus {
  font-size: 20px;
  color: #4a7dff;
}
.img-hint {
  font-size: 11px;
  color: #666;
}
.img-progress {
  position: relative;
  height: 6px;
  background: #26263a;
  border-radius: 3px;
  overflow: hidden;
  margin-top: 6px;
}
.img-progress-fill {
  height: 100%;
  background: #4a7dff;
  transition: width 0.1s;
}
.img-progress-txt {
  position: absolute;
  right: 2px;
  top: -14px;
  font-size: 10px;
  color: #9ab8ff;
}
.img-err {
  margin-top: 6px;
  font-size: 11px;
  color: #ff6b6b;
  word-break: break-word;
}
</style>
