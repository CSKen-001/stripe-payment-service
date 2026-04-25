# stripe-payment-service

汎用 Stripe Webhook サーバー。複数のアプリで共有して使えます。

## アーキテクチャ

```
Stripe → [stripe-payment-service] ──POST──► Callback URL（各アプリ）
                  │
                  └── PostgreSQL / SQLite（Stripeデータのみ保存）
```

各アプリは **Callback URL を1つ実装するだけ** で Stripe 決済を利用できます。

## セットアップ

```bash
# 依存パッケージのインストール
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 環境変数の設定
cp .env.example .env
# .env を編集して Stripe キーと Callback URL を設定

# 起動
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 環境変数

| 変数 | 必須 | 説明 |
|------|------|------|
| `STRIPE_SECRET_KEY` | ✅ | `sk_test_...` または `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | ✅ | `whsec_...` |
| `CALLBACK_URL` | 推奨 | アプリ側の通知エンドポイント |
| `PRICE_MAP_JSON` | 推奨 | Price ID → プラン名マッピング（JSON） |
| `DATABASE_URL` | | デフォルト: SQLite |
| `CALLBACK_SECRET` | | Callback 認証用 HMAC シークレット |

詳細は `.env.example` を参照。

## Price ID のマッピング設定

```bash
PRICE_MAP_JSON={"price_xxx": "standard", "price_yyy": "pro", "price_zzz": "enterprise"}
```

## Callback の実装

このサービスが Stripe イベントを処理すると、`CALLBACK_URL` に POST します。

**リクエスト例:**

```json
{
  "event_type": "customer.subscription.created",
  "stripe_event_id": "evt_xxx",
  "stripe_customer_id": "cus_xxx",
  "plan": "pro",
  "subscription_id": "sub_xxx",
  "invoice_id": null
}
```

**`CALLBACK_SECRET` を設定した場合、リクエストヘッダーに署名が付きます:**

```
X-Webhook-Signature: sha256=<hmac-sha256>
```

検証例（Python）:

```python
import hashlib, hmac

def verify_callback(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

**各アプリ（例: FastAPI）の実装例:**

```python
@app.post("/api/payment-callback")
async def payment_callback(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Webhook-Signature")
    if not verify_callback(body, sig, CALLBACK_SECRET):
        raise HTTPException(status_code=401)

    data = await request.json()
    customer_id = data["stripe_customer_id"]
    plan = data.get("plan")

    # ここに各アプリのビジネスロジックを実装
    if plan and plan != "free":
        await grant_plan_access(customer_id, plan)
    elif plan == "free":
        await revoke_plan_access(customer_id)

    return {"ok": True}
```

## 対応 Stripe イベント

| イベント | 説明 |
|----------|------|
| `checkout.session.completed` | チェックアウト完了 |
| `payment_intent.succeeded` | 決済成功 |
| `payment_intent.payment_failed` | 決済失敗 |
| `invoice.paid` / `invoice.payment_succeeded` | 請求書支払い完了 |
| `invoice.payment_failed` | 請求書支払い失敗 |
| `customer.subscription.created` | サブスク作成 |
| `customer.subscription.updated` | サブスク更新 |
| `customer.subscription.deleted` | サブスク解約（plan="free" で通知） |
| `customer.*` | 顧客情報更新 |

## エンドポイント

| パス | 説明 |
|------|------|
| `POST /webhook` | Stripe Webhook 受信 |
| `GET /healthz` | ヘルスチェック |
| `GET /health/detailed` | DB・Stripe 接続確認 |
| `GET /metrics` | Prometheus 形式メトリクス |

## Stripe Webhook の設定

Stripe ダッシュボード → Developers → Webhooks で、
このサービスの `https://your-domain.com/webhook` を登録してください。

## 本番デプロイ（Gunicorn）

```bash
gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```
