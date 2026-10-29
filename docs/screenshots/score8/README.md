# لقطات قبل/بعد — الشاشات الخمس الحرجة (Score-8)

طريقة الالتقاط (يدوية، موثّقة): نفس قاعدة بيانات seed التجريبية ونفس الـAPI الخلفي في الحالتين،
الفرق الوحيد هو الواجهة: `before/` مبنية من فرع `main` (قبل السبرينت، worktree عند `0e4134b`)،
و`after/` مبنية من فرع السبرينت الحالي. تسجيل دخول حقيقي عبر النموذج بحساب المدير التجريبي،
مقاس ثابت 1440×900، لغة `ar-EG`.

الأوامر (من جذر المستودع):

```bash
# API بخادم بيانات seed (SQLite مؤقت)
cd backend && DATABASE_URL="sqlite+aiosqlite:////tmp/shots.db" INTEGRATIONS_DRIVER=sim ENV=dev \
  .venv/bin/python -m alembic -c alembic.ini upgrade head && \
  DATABASE_URL="sqlite+aiosqlite:////tmp/shots.db" INTEGRATIONS_DRIVER=sim ENV=dev .venv/bin/python -m app.seed
DATABASE_URL="sqlite+aiosqlite:////tmp/shots.db" INTEGRATIONS_DRIVER=sim ENV=dev \
  .venv/bin/uvicorn app.main:app --port 8030
# الواجهتان
cd frontend && VITE_API_ORIGIN=http://127.0.0.1:8030 npm run build && \
  VITE_API_ORIGIN=http://127.0.0.1:8030 VITE_PREVIEW_PORT=5174 npx vite preview --port 5174
git worktree add /tmp/bh-main main   # + نفس خطوات البناء على المنفذ 5175
# الالتقاط بمتصفح Chromium عبر Playwright (نفس مسارات e2e/fixtures.ts)
```

الشاشات: `01-dashboard` (لوحة اليوم الجديدة في «بعد»)، `02-inbox`، `03-erp`
(بطاقة إقفال الفترات وزر تصدير Excel في «بعد»)، `04-client-profile`، `05-login`.
مجلد `compare/` يحمل تركيبًا جنبًا إلى جنب لكل شاشة.
