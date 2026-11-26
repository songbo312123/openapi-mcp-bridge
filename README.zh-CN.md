# openapi-mcp-bridge

**把两千个接口交给 AI，但不给它两千个工具。**

[English](README.md)

---

## 问题

真正大的内部平台不会只有十个接口。它有几百上千个，分散在十几个服务里，
一半没有文档，而你手上那份导出还有几十处是错的。

于是两件事同时坏掉：

- **人记不住**——找一个接口变成考古；
- **模型也装不下**——把接口全注册成工具，选择质量崩塌、token 成本爆炸；
  把 spec 塞进 prompt，问题还没问上下文就没了。

这个项目的做法是第三条路：**给语料建索引，只交给模型三个动作，让它自己搜。**

```
list_apis(keyword)      → 一小段过滤后的列表
describe_api(name)      → 选中那个接口的完整签名
call_api(name, params)  → 真正调用，自动路由 + 护栏
```

模型手里永远只拿着和当前任务相关的那几个接口。语料有多大，不再重要。

---

## 快速开始

完全离线，不需要账号、凭据或网络。

```bash
pip install -e .
python examples/mock_api.py      # 终端 1：一个用完即弃的内存 API
python examples/quickstart.py    # 终端 2
```

你会看到：语料被索引、创建一条笔记、一个 override 修正了过期的路由、
以及一次 `DELETE` 因为没人确认而被拒绝。

---

## 四个设计决定

### 1. 检索优于注册

给每个接口注册一个工具，等于把整个语料搬进 prompt；检索只搬答案。
代价是多一次往返，收益是接口涨到一万个时方案依然成立。

还有一个副作用：**检索失败是可见的**。模型找不到就是找不到，
而不是在四十个看起来都合理的工具里悄悄选了个第二好的。

### 2. 护栏写在代码里，不写在提示词里

提示词里写"删除前先确认"是建议。下面这个是强制：

```python
if method not in SAFE_METHODS and self.confirm_writes and not confirm:
    raise PermissionError(f"{method} {path} writes data and needs explicit confirm=True")
```

风险按接口分级，不按 HTTP 方法猜。只读的 `POST`（搜索、预览、报表）
标成 `read`，**不**要求确认——因为对日常查询强制确认，会把人训练成条件反射式
点确认，而一个条件反射的护栏等于没有护栏。`DELETE`、导出、支付标成
`dangerous`，无论谁调用都要确认。

### 3. 修正放在导出文件之外

导出的 spec 是生成物，改了下次导出就没了。所以偏差记在 `overrides.yaml` 里，
按原始的 `(project, method, path)` 索引，**每一条都必须写 `reason`**：

```yaml
overrides:
  - project: notes
    method: GET
    path: /notes/legacy
    set:
      method: POST
    reason: >-
      导出里还是 GET，但 handler 只注册了 POST。
```

几个月后，这个文件会是仓库里最值钱的东西——它记录的是文档和真实系统
在哪儿不一致、以及为什么。没有强制 `reason`，它会退化成一堆没人敢删的补丁。

### 4. 所有平台相关的东西都是钩子

服务路由、凭据、签名规则，各家差别极大。与其硬编码，不如留两个缝：

```python
class MyAuth(AuthProvider):
    def headers(self):   return {"Authorization": f"Bearer {self.token}"}
    def sign(self, params): return {"sig": compute_sig(params)}
```

传输层不认识你的签名算法，你的签名算法也不认识传输层。

---

## 接入自己的语料

1. 从网关导出 OpenAPI 3（或手写）。
2. 放进 `specs/`。
3. 给接口补元信息：

   ```yaml
   x-project: billing          # 逻辑项目，用于消歧
   x-service: billing-api      # 打到哪个 host
   x-auth: token               # none | token | ...
   x-risk: read                # read | write | dangerous
   x-content-type: json        # form | json
   ```

   `x-service` 可以打在文档、路径或单个 operation 上。这正是 OpenAPI
   本身没有的概念——一份 spec 描述的是分散在多个 host 上的接口。
4. 在 `config.yaml` 的 `services:` 里登记 host。
5. `python -m openapi_bridge.registry` 确认索引认得它。

---

## 现状与边界

- 37 个离线测试，不依赖网络（`pytest -q`）。
- 自带语料是 Petstore 加一个本地 mock，故意做得很小——README 里写的每个行为
  都能在一分钟内验证。
- 检索是子串匹配。单项目超过几百个接口后，需要先对 `summary` 做向量召回
  再精排。这是明确的下一步，不是藏着没说的缺口。
- 还没有缓存、重试/退避、响应 schema 校验。

真实内部部署的规模：**12 个服务、2023 个接口**，单进程索引，
四个月积累下 40+ 条实测 override。

---

## 许可

MIT。
