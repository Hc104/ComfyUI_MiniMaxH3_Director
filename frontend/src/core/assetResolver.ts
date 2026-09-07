/**
 * DirectorCore — Asset Resolver（模型无关）。
 *
 * 职责：@资产识别。扫描提示词文本，命中资产库（name/aliases）→ 资产引用列表。
 * 这是「@资产识别」核心交互（蓝图 §7.4）的基础。
 *
 * 与后端 gen_timeline.py 命名匹配规则对齐：纯子串包含、名长 ≥ 2、大小写不敏感。
 */

import type { Asset, AssetKind } from "@/models/project";

/** 资产库索引：小写 name/alias → 资产 id 列表。 */
export interface AssetIndex {
  byName: Map<string, string[]>;
  assets: Asset[];
}

/** 由资产列表构建索引（同一资产可被 name 和多个 aliases 命中）。 */
export function buildAssetIndex(assets: Asset[]): AssetIndex {
  const byName = new Map<string, string[]>();
  const push = (name: string, id: string) => {
    const k = name.trim().toLowerCase();
    if (k.length < 2) return; // 后端规则：名长 ≥ 2
    const list = byName.get(k) ?? [];
    if (!list.includes(id)) list.push(id);
    byName.set(k, list);
  };
  for (const a of assets) {
    push(a.name, a.id);
    for (const al of a.aliases ?? []) push(al, a.id);
  }
  return { byName, assets };
}

/** 一次命中：某资产在文本中的引用区间（含 @ 符号）。 */
export interface MentionHit {
  assetId: string;
  name: string;
  kind: AssetKind;
  /** 命中起点（含 @）。 */
  start: number;
  /** 命中终点（不含 @ 之后的空白）。 */
  end: number;
}

const MENTION_RE = /@([\p{L}\p{N}_\-（）()\s·.·]+)/gu;

/**
 * @资产解析：扫描 text 中 @名字 片段，做子串包含匹配资产库。
 *
 * 规则（与后端一致）：
 * - @ 后接一段连续文本，将其小写后与资产库 name/alias 做**子串包含**匹配；
 * - 名长 ≥ 2 才参与匹配（避免单字误命中）；
 * - 命中资产按出现顺序返回；同一资产同一区间去重。
 *
 * 例：`@林雪走进@霓虹街区` + 资产「林雪」「霓虹街区」→ 2 个命中。
 */
export function resolveMentions(text: string, index: AssetIndex): MentionHit[] {
  if (!text) return [];
  const hits: MentionHit[] = [];
  let m: RegExpExecArray | null;
  MENTION_RE.lastIndex = 0;
  while ((m = MENTION_RE.exec(text)) !== null) {
    const raw = m[1].trim();
    if (raw.length < 2) continue;
    const lower = raw.toLowerCase();
    // 同一 @ 片段内，同一资产只命中一次（name 与 alias 同时命中时保留先遇到的较长匹配）。
    const seenInSegment = new Set<string>();
    for (const [name, ids] of index.byName) {
      if (name.length < 2 || name.length > lower.length) continue;
      const hitIdx = lower.indexOf(name);
      if (hitIdx === -1) continue;
      for (const id of ids) {
        if (seenInSegment.has(id)) continue;
        const asset = index.assets.find((a) => a.id === id);
        if (!asset) continue;
        seenInSegment.add(id);
        hits.push({
          assetId: id,
          name: asset.name,
          kind: asset.kind,
          start: m.index + 1 + hitIdx,
          end: m.index + 1 + hitIdx + name.length,
        });
      }
    }
  }
  // 去重：同资产同区间只保留一条（@ 命中 name + alias 都指向同一资产时）。
  const seen = new Set<string>();
  return hits.filter((h) => {
    const key = `${h.assetId}:${h.start}:${h.end}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
