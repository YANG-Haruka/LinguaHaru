# 多项目翻译质量系统调研（代码级横向对比）

> 调研对象：**GalTransl、LinguaGacha、AiNiee、BallonsTranslator、SakuraLLM 生态、
> llm-subtrans、manga-image-translator、VNTranslationTools**。按 9 个维度横向对比，
> 给出每项目做法 + "最佳做法" + 对 LinguaHaru 的采纳建议。
> 状态：**供你 review**，确认后并入翻译重写设计。

标记：**[E]** = 源码确认；**[I]** = 推断。

---

## 仓库地图
| 项目 | 仓库 | 后端目录 |
|---|---|---|
| GalTransl | `xd2333/GalTransl` | `GalTransl/` |
| LinguaGacha | `neavo/LinguaGacha`（Python 在 tag `MANUAL_BUILD_v0.60.1`；main 已转 TS/Electron） | `module/`,`resource/` |
| AiNiee | `NEKOparapa/AiNiee` | `ModuleFolders/`,`Resource/` |
| BallonsTranslator | `dmMaze/BallonsTranslator` | `ballontranslator/modules/translators/` |
| SakuraLLM | `SakuraLLM/SakuraLLM` | `utils/`,`translate_novel.py` |
| llm-subtrans | `machinewrapped/llm-subtrans` | `PySubtrans/` |
| manga-image-translator | `zyddnys/manga-image-translator` | `manga_translator/translators/` |
| VNTranslationTools | `arcusmaximus/VNTranslationTools` | `VNTextPatch.Shared/`(C#) |

---

## 维度 1 — 分段 / 分块
- **GalTransl**[E]：两级（文件级 `DictionaryCountSplitter(2048)` → 每请求 16 条）；`contextNum=8` 条历史译文作上下文；引号跨条用 `analyse_dialogue` 缝合；`\n→<br>`、`\t→[t]`。
- **LinguaGacha**[E]：**行 + token 同时**（`line_limit=max(8, token//16)`，谁先到谁触发）；上文**只回溯到句末标点结尾的行**（句子完整性的关键）。
- **AiNiee**[E]：行 或 token 可切换；`pre_line_counts` 上文（平铺）。
- **llm-subtrans**[E]：**场景**(静音>60s 切) → **批次**(在最大内部间隙递归二分，10–30 行)；**滚动验证摘要链**喂下文。
- manga[E]：字符数 `MAX_TOKENS*4`；跨页上下文默认 0。SakuraLLM[E]：整行打包到 512 字符。VNT[E]：按引擎语法切，一显示行=一单元。

**最佳**：llm-subtrans 的**场景/批次层级 + 滚动摘要链**(字幕/长文最佳)；LinguaGacha 的**句末标点门控上文**(廉价避免半句上下文)；GalTransl 的 `cross_num` 重叠窗口。

## 维度 2 — 每请求上限 + 自适应
- AiNiee[E]：`tokens_limit=1024`，**每轮减半**(round_limit=10)。
- **LinguaGacha**[E]：per-model `input_token_limit=512`；**几何收缩** `factor=(16/t0)**0.25` 降到单条；单条重试<3 次后 `force_accept`(dst=src 标 ERROR)。
- GalTransl[E]：16 行/请求；动态(默认关)：失败减半、3 轮干净后 +1，夹 8/64。
- manga[E]：递归减半 `_MAX_SPLIT_ATTEMPTS=3`。

**最佳**：**LinguaGacha 的几何收缩 + 单条有限重试 + 终态 force_accept**(最优雅、保留部分进度、必终止)；批次上限**放模型配置**而非全局。

## 维度 3 — 失败检测/校验（差异最大）
全部检查项及拥有者[E]：
- **行数/索引==输入**：所有项目。
- **索引键匹配+重排**：BallonsTranslator/manga/llm-subtrans。
- ⭐**逐行随机锚(sig)防串行**：**GalTransl** —— 每行随机 3 字符标记必须回显，抓"模型偷偷打乱/丢行"(行数检查抓不到的)。
- **译==原**：llm-subtrans(NFC+strip)/AiNiee(Jaccard≥0.85)/LinguaGacha(子串或 Jaccard>0.80)。
- **残留源语言脚本**：GalTransl/AiNiee/LinguaGacha/manga。
- **重复/退化/幻觉**：SakuraLLM("撞 max_tokens=退化")/manga(连续≥20)/BallonsTranslator(`(.{n})\1+`)/LinguaGacha(流式周期1/2/3检测,阈值50)/GalTransl(词频>20)。
- **占位符/标签保留**、**长度比**、**换行数**、**JSON 合法**、**标点增删**、**术语遵守**、**few-shot 回显泄漏**：各有覆盖。

**重试策略**：GalTransl 阶梯(切⅓→清历史→`(Failed)`兜底);llm-subtrans(回放错误+temp+0.1);多项目用 `frequency_penalty` 升级治退化;**LinguaGacha 两层(驱动重试 vs 仅警告)+ 部分行逐行接受**。

**最佳**：① GalTransl **逐行 sig 锚**(最巧,抓批内串扰);② GalTransl `Problem.py` **事后 QA 清单 → retranslKey 重排**;③ LinguaGacha **流式退化检测 + 部分行接受 + 两层校验**;④ SakuraLLM **"撞 max_tokens=退化"**(近乎免费);⑤ llm-subtrans **索引键匹配 + 重排检测 + temp+0.1**。

## 维度 4 — 术语表/词典
- **GalTransl**[E]：**3 角色词典**(preDict 源替换 / GPT-dict 进 prompt / postDict 输出替换);**只注入命中项**;note 含性别/类型。
- LinguaGacha[E]：JSON `{src,dst,info,regex,case}`;只注入命中;冲突 OVERWRITE/FILL_EMPTY。
- AiNiee[E]：`collect_matched_rows` 只注入命中;性别在单独角色表。
- manga[E]：模糊匹配级联(假名归一 Levenshtein)。

**最佳**：**只注入命中项**(所有严肃项目共识,省 token+提升遵守);GalTransl **三角色拆分**(确定替换 + LLM 软引导);manga **模糊匹配**(应对变形)。

## 维度 5 — 专名/术语提取(预处理)
- **AiNiee**[E]：最丰富,map-reduce 提取 **3 类**(角色 src/译/性别/备注、术语 类目路径、禁翻标记)→ 注入 3 张表。
- GalTransl[E]：Vaporetto 分词 + 片假名正则 → 词频过滤 → **集合覆盖选代表句** → LLM 生成 → **跨块投票** 去重;另有角色名表。
- LinguaGacha[E]：投票聚合 + 自动导入 + **假名注入伪装控制码**让提取器忽略。
- llm-subtrans[E]：模型 emit 术语 → **拿批次当 ground truth 校验**(拒绝幻觉键)。

**最佳**：AiNiee **3 类提取**;GalTransl **集合覆盖+投票**(省钱);llm-subtrans **校验 LLM 提取的术语**(必备护栏)。

## 维度 6 — 前/后处理
- **LinguaGacha**[E]：最丰富 **Fixer 套件**(假名/谚文/代码/转义/数字①–⑳/标点,**按源-输出计数差驱动修复**)+ RubyCleaner + NFC + 全半角表;默认开。无 OpenCC。
- GalTransl[E]：`<br>`/`[t]` 编码-还原 + OpenCC + 引号/省略号修。
- AiNiee[E]：NFC + 全半角 + 占位符掩码 + OpenCC(默认关)。
- llm-subtrans[E]：仅 CJK 相邻处转全角标点;时长切行 `max_line_duration=4s`。

**最佳**：**LinguaGacha 计数驱动 Fixer 套件**(廉价、无 LLM、对症);GalTransl **编码-还原**结构保护。

## 维度 7 — 不译/代码标签保护
- **VNTranslationTools**[E]：最强,**结构级**(只有 Message/CharacterName 类型 span 进翻译,操作码留二进制);内联标签白名单。
- AiNiee[E]：3 层(通用过滤 + 特殊 + 用户 NTL 禁翻表,**掩码+prompt表+事后审计 三重**)。
- LinguaGacha[E]：**按文件类型的 preserve 正则预设**(rpgmaker/renpy/kag/wolf)。

**最佳**：VNT 哲学(只译类型化 span)最稳但需格式解析器(重);实用等价 = **占位符掩码 + 按文件类型 preserve 预设 + 事后"掩码是否存活"审计**。LinguaHaru 已有 `placeholder_mask.py`,缺**文件类型预设**和**事后审计**。

## 维度 8 — 润色/质量优化
- AiNiee[E]：**完整 LLM 润色**(源+初译→润色)。
- GalTransl[E]：**校对 pass**(emit `newdst` + `Rivision:` 自我批评)+ **retranslKey 只重排被标记行**。
- LinguaGacha[E]：**无 LLM 润色**(rewrite 砍掉了)——改用规则 Fixer + 人工校对。

**最佳**：GalTransl **定向重排**(只修 QA 标记的行,最省);AiNiee 二遍润色(重量级,可选);⭐**信号:LinguaGacha 新版砍掉 LLM 润色** → 暗示通用内容下"廉价规则修复+定向重排"性价比胜过整篇二遍 LLM。

## 维度 9 — 语言习惯/风格
- **GalTransl**[E]：**可换的文件式风格指南**(`日译中_增强v2.md`:反翻译腔规则 + 角色原型语气库);对话 vs 独白。
- AiNiee[E]：结构化块(角色设定/世界观/翻译风格)。
- manga[E]：**按语言的 few-shot** + 罗马音敬语示例("Karai-san" GOOD / "Mr. Karai" BAD)。
- llm-subtrans[E]：字幕约束(max_chars=120/单行44/max_newlines=2/时长);**无真 CPS 指标**[I]。

**最佳**：GalTransl **可换文件式风格指南 + 反翻译腔规则**(纯 prompt,廉价);manga **按语言 few-shot + 罗马音敬语**;⭐**字幕:实现真正的 CPS(每秒字符)阅读速度检查——所有被调研工具都没做,LinguaHaru 可成最佳**。

---

## 对 LinguaHaru 的采纳优先级（按性价比）

### Tier 1（先做,高价值+廉价)
1. **术语表只注入命中项**(共识)——省 token + 提升遵守。
2. **事后 QA 清单(à la GalTransl Problem.py)**——残留源语言/译==原(Jaccard)/换行数/长度比/占位符存活/术语遵守 → 写进 `coverage.json`(已有)。
3. **逐行防串行锚(GalTransl sig)**——批量时让模型回显行 id,抓串扰/丢行。
4. **退化守卫(SakuraLLM 启发式 + frequency_penalty 升级)**——撞 token 上限或周期重复→升 penalty 重试(本地/Sakura 模型关键)。
5. **可换文件式风格指南 + 反翻译腔规则**(纯 prompt,已有 `config/prompts/`)。

### Tier 2（次做,中等)
6. **计数驱动 Fixer 套件(LinguaGacha)**——假名/数字/标点/代码计数差→确定修复(配 #2)。
7. **句末标点门控上文(LinguaGacha)**——只喂句末结尾的上文。
8. **优雅失败收缩(LinguaGacha 几何收缩+单条重试+force_accept)**——替代粗暴减半,保留部分进度且必终止。
9. **两层校验(驱动重试 vs 警告)+ 部分行逐行接受**——别因一行重做整批。
10. **文件类型 preserve 预设 + NTL(掩码+prompt+审计)**——扩展现有 placeholder_mask。
11. **定向重排(GalTransl retranslKey)**——只重译 QA 标记的行(接你的续传/历史)。

### Tier 3（高价值但重)
12. **术语/角色提取预处理(AiNiee 3 类 / GalTransl 集合覆盖+投票)**——额外 LLM pass,长文/游戏一致性最强;用集合覆盖省钱 + llm-subtrans 校验拒幻觉。
13. **滚动验证摘要链(llm-subtrans)**——场景/批次层级 + 摘要前喂(字幕/长文)。
14. **可选二遍 LLM 润色(AiNiee/GalTransl)**——质量最佳但翻倍成本;只配合 #11 重译标记行;LinguaGacha 砍掉它=通用内容下廉价路径胜。

### 字幕专属
15. **行长/换行/时长上限(llm-subtrans)**。
16. ⭐**实现真 CPS(每秒字符)阅读速度检查**——没人做,LinguaHaru 可领先。

---

## 重要校验/坑[E-verified]
- GalTransl 流内退化检测 + token re-split 是**死代码**(main 没接);活跃防重复=frequency_penalty + 事后 Problem.py。
- LinguaGacha Python 停在 tag,main 已转 TS。
- manga 活跃 OpenAITranslator 走字符路径(token-aware 基类没接)。
