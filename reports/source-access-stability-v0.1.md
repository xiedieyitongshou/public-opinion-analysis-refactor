# Source Access Stability v0.1

Generated at: 2026-09-06

Last rechecked: 2026-09-06 16:46 Asia/Shanghai

## Scope

Day 15 engineering feasibility check for selected sources:

- 人民网
- 中国新闻网
- 新华网
- 微博热搜
- 知乎热榜

This check separates two questions:

- Can the source provide structured data?
- Can the project obtain that data through a stable, low-risk, no-login path?

## Current Result

| Source | Structured data observed | Stable no-login path | Engineering status | Notes |
|---|---|---|---|---|
| 人民网 | Yes | Yes | `use` | RSS returns parseable items with title, URL, published time, summary, author. |
| 中国新闻网 | Yes | Yes | `use` | RSS returns parseable items; collector should tolerate malformed XML such as bare ampersands. |
| 新华网 | Yes | Partial | `fallback` | RSS returns title, URL, summary, author; current endpoint lacks published time. |
| 微博热搜 | No | No | `postpone` | Public web/API probes redirect to visitor/login or return forbidden. Stable access likely requires official API or authorized account flow. |
| 知乎热榜 | No | No | `postpone` | Public page/API probes return 403/401. Stable access should use official data platform if available and approved. |

## Engineering Decision

- Day 15 can confirm the three official/news sources as usable structured sources, with Xinhua kept as fallback until a better endpoint or time parsing path is found.
- Day 15 cannot confirm Weibo and Zhihu as stable no-login structured sources.
- Weibo/Zhihu should not enter Week 4 real collector development unless an official, authorized, rate-limited API path is confirmed.
- Do not mark the full five-source set as stable just to satisfy the product goal. The current stable real-source set is two `use` official sources plus one `fallback` official source.
- Do not use personal account cookies, automated login, proxy pools, or anti-bot bypass for the public repo implementation.
- If Weibo/Zhihu remain `postpone`, use mock community source or find a lower-risk text hotlist substitute for Week 4.

## Risk Notes

- Login-cookie crawling makes the project depend on account state and platform anti-abuse controls.
- Account-based automated collection can trigger verification, temporary restriction, or account suspension depending on platform rules, endpoint sensitivity, traffic pattern, and whether the access method violates terms.
- Even low-frequency collection is not automatically safe if it violates platform terms or uses non-public endpoints.
- The stable path for community sources should be official API, authorized data platform, or a clearly public low-frequency endpoint.

## Day 15 Gate Result

Proceed to Week 4 real collector development with:

- 人民网: `use`
- 中国新闻网: `use`
- 新华网: `fallback`

Do not proceed to Week 4 real collector development with:

- 微博热搜: `postpone`
- 知乎热榜: `postpone`

Day 16 should either validate an official authorized API/data-platform path for Weibo/Zhihu or choose a lower-risk text hotlist substitute. If neither is available, implement `mock community source` for the community-signal branch while keeping the real collector limited to the official/news sources.
