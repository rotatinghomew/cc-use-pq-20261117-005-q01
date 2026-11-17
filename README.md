# 叙事母题证据台账

这是一个供文学课程与民间叙事研究小组使用的本地分析工具。研究者导入有来源说明的故事结构记录，逐条标注判断及其事件依据，再查看标注共识、未决覆盖率和可回溯报告。工具不自动解释文学意义，也不把新闻评论中的解释当作已验证结论。

## 运行

需要 Python 3.11 或更新版本，无第三方依赖。

从项目根目录运行：

```sh
PYTHONPATH=src python -m story_patterns.cli demo --database /tmp/story-patterns.sqlite3 --fixture fixtures/demo.json
```

也可分别使用 `import`、`decide` 与 `report` 子命令处理 JSON 文件。每个故事修订版包含来源、角色顺序及连续事件；每项判断必须引用该修订版中的事件。批次标识支持安全重试，标注标识与标注员序号不可覆盖既有内容。

支持包含故事对象的 JSON 数组或逐行 NDJSON 导入；这两种格式需使用 `--batch-id`。`queue` 展示未评、待补共识、分歧及已达共识的样本，并列出尚未被判断引用的事件。`report` 同时给出标注员两两一致率及事件证据覆盖率；这些是描述性指标，不替代人工解释。报告以内容指纹保存，可用 `export --report-id ... --format json|csv --output ...` 输出；默认拒绝覆盖现有文件，显式 `--force` 才替换。`audit` 检查 SQLite 内部与外键完整性。

导入前可以用 `preview` 汇总每条记录的校验错误，不写数据库；正式导入仍坚持整批事务。`lineage` 展示已录入的修订版本与缺失序号。`compare` 按传统标签分层输出描述性比例、共识数与覆盖率；不同分组的分母可能不同，结果不作因果或总体推断。

`manifest` 汇总导入批次、故事修订和来源引用。保存报告还包含读者剔除敏感性检查，提示结论是否依赖某位读者；该诊断不等同于置信区间。报告可导出为 Markdown (`--format md`) 供课程讨论。

`audit` 除 SQLite 外键和数据库完整性检查外，也会复核故事文档与索引列、导入批次摘要、标注证据、仲裁摘要及已保存报告的内容指纹是否一致。修订对比会标出角色、顺序、来源和事件内容变化；每个版本独立标注，系统不把旧版判断自动复制到新版。

报告列出最新有效读者的逐标签投票数和每位读者覆盖的修订数；被后续序号取代的旧判断仍保留在 `history`，但不会重复进入统计。

## 角色别名归并（跨译本比较）

不同译本、转写或抄本常给同一角色使用不同称呼（如"阿甲/长兄/阿大"）。台账把这类归并做成**分析叠加层**，而不是改写录入内容：

- 原始故事中的 `characters[].name`、事件文本、读者判断和仲裁记录永不改变；`search`、`manifest`、`history` 与逐修订覆盖率仍按原始称呼和原始事件计算。
- 分析人员用 `alias-add` 按"传统 + 原始称呼"声明映射到规范人物（`canonical_id` + `canonical_name`）；报告、比较的**人物口径**部分按规范人物汇总。匹配是精确的传统名与称呼原文，不做大小写或字形折叠。
- 规范人物名与"同一传统内的活跃原始称呼"都必须唯一。若同一修订内两个角色被映射到同一规范人物，或两个角色本来就同名，报告会在 `character_view.collisions` 中标记该修订，并将其排除出规范人物汇总，不会猜测对应关系。
- `alias-revoke` 撤销映射时保留原记录、撤销人和理由；撤销后可用新 `alias_id` 重新声明。重试同一条记录是幂等的；同标识改内容会报冲突。

每次报告都会冻结**当时实际生效、且与本报告所选修订相关**的归并口径（`provenance.character_policy` 与 `character_policy_id`），并记录人物覆盖率与分母：

- `raw_character_entries`：原始角色条目数；`aliased_character_entries` 经别名解析；`unmapped_character_entries` 保持原样单列。
- `collision_story_revisions` / `collision_character_entries`：因重名或归并冲突而未进入规范人物汇总的修订与条目。
- `compared_story_revisions`、`analysed_character_entries`、`distinct_analysis_characters`：实际参与汇总的分母。
- 每位规范人物按传统分别列出出现修订数、事件数、被判断引用的事件数及事件覆盖率；`compare` 的每个分组也带有自己的 `character_policy_id`、未映射称呼与冲突列表。

`alias-list` 展示全部历史（含已撤销）与当前活跃口径。别名输入是 JSON 文件：

```json
{"alias_id": "coast-elder", "tradition": "coastal_copy", "raw_name": "阿甲",
 "canonical_id": "elder", "canonical_name": "长兄",
 "rationale": "与内陆转写的第一事件同位置。", "created_by": "editor-lin"}
```

撤销同样用 JSON 文件（`alias_id`、`revoked_by`、`revocation_reason`），通过
`alias-add --database ... --file ...` / `alias-revoke --database ... --file ...` / `alias-list --database ...` 执行；`alias-list --active-only` 只看当前生效映射。

### 复核历史结果

`replay --report-id ...` 用报告自身记录的修订清单、判断编号与仲裁编号重新计算：

- `reproduces_stored_report`：按报告冻结的口径能否逐字节复现原报告（即 `replayed_report_id` 与原 `report_id` 一致）。不一致说明存储内容被改动，`audit` 也会报出。
- `story_revisions_replayed`：复算使用的修订；若某个修订已不可用，命令报冲突而非静默换用新版。
- `newer_decisions_not_used` / `newer_adjudications_not_used`：报告之后新增的判断或仲裁，**不会**混入旧报告复算。`missing_decisions`/`missing_adjudications` 列出已丢失但被报告引用的记录。
- `stored_summary`、`replayed_summary`：原报告与冻结口径复算的结论、覆盖率与人物分母，应与原报告一致。
- `current_policy_summary`、`policy_differences`、`character_differences`：若之后新增或撤销了映射，这里给出"按当前口径重算"的对照，并逐条列出新增/移除/改指向的映射及其别名编号，以及受影响的规范人物统计。它只是对照，不会回写旧报告，也不会改绑任何读者判断。
- `decisions_unchanged`：确认复算引用的判断与仲裁集合与原报告一致。

复核步骤建议：先用 `replay` 确认可复现且 `decisions_unchanged` 为真；再查看 `policy_differences` 判断新旧结论差异是否只来自归并口径；最后在 `alias-list` 中核对相关映射的 `rationale`、`created_by` 与被撤销记录。若要更新结论，应重新生成报告并引用新的 `report_id`，而不是修改旧报告。

`audit` 除原有检查外，也校验别名记录与规范人物的摘要、活跃映射唯一性、撤销归属，以及每份报告冻结口径的 `policy_id` 是否与其内容一致。

演示文件完全由本地合成材料构成，并非新闻报道或已出版故事的原文。`fixtures/multitranslation.json` 是合成的多译本与冲突案例，包含别名归并、同名角色、修订重导与映射撤销；`demo --fixture` 会依次导入并输出归并前后报告与复算结果。

若独立标注存在分歧，编辑可用 `adjudicate` 提交单独的处理记录。它要求引用当前修订事件与简短理由，不覆盖任何读者投票；报告并列保留独立共识、最终采用标签及仲裁依据。`history` 可查看一位读者此前的判断和所有仲裁版本。

报告分别统计“平行型/加害型”判断和最小者结局判断。`unclear` 与 `unknown` 不进入对应投票，但覆盖表会保留同意、分歧和样本不足数量。报告列出参与故事修订及判断编号，便于复核。数据库仅保存在用户指定的本地路径。

演示文件完全由本地合成材料构成，并非新闻报道或已出版故事的原文。

## 检查

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m compileall -q src
PYTHONPATH=src python -m story_patterns.cli demo --database /tmp/story-patterns-check.sqlite3 --fixture fixtures/demo.json
PYTHONPATH=src python -m story_patterns.cli demo --database /tmp/story-patterns-aliases.sqlite3 --fixture fixtures/multitranslation.json
```
