# Threads と X の自動投稿の準備

インスタのカルーセルと同じ時刻に、Threads と X にも投稿します。承認はインスタと共通（GitHub の「Review deployments」1回）です。
鍵が入っていない間は、投稿をスキップするだけで、インスタの投稿には影響しません。

## Threads（無料）

1. Threads のアカウントを、インスタ（あいの大人の美容ノート）から作っておく
2. [Meta for Developers](https://developers.facebook.com/apps/) で、インスタ用に作ったアプリを開く（または新しく作る）
3. 「ユースケースを追加」→「Threads APIにアクセス」を追加し、権限 `threads_basic` と `threads_content_publish` をオンにする
4. 「役割」→「Threadsテスター」に自分の Threads アカウントを追加し、Threads アプリの「設定 → アカウント → ウェブサイトのアクセス許可 → 招待」で承認する
5. 「Threads API のユースケース → 設定」から「ユーザートークン生成ツール」で自分のアカウントの鍵を作る（長期トークン、約60日有効）
6. GitHub のリポジトリ → Settings → Secrets and variables → Actions に、`THREADS_ACCESS_TOKEN` という名前で保存する

鍵の延長は毎週月曜 3:00 に自動で行います（インスタの鍵と同じ仕組み）。

## X（従量課金・前払い）

X の API は 2026年から前払いの従量課金です。文字だけの投稿は1回約 0.015ドル（URL入りは約 0.20ドル）なので、月26回でおよそ 0.4ドルです。

1. [X Developer Console](https://console.x.com/) に、投稿したい X アカウントでログインし、開発者登録をする
2. クレジットを少額（5ドルなど）購入し、使いすぎ防止の上限（Spending limit）を設定する
3. アプリを作り、「User authentication settings」で権限を **Read and write** にする（Read only のままだと投稿できない）
4. 「Keys and tokens」で次の4つを作る。権限を変えたあとに Access Token を作り直すこと
   - API Key → `X_API_KEY`
   - API Key Secret → `X_API_SECRET`
   - Access Token → `X_ACCESS_TOKEN`
   - Access Token Secret → `X_ACCESS_TOKEN_SECRET`
5. 4つとも GitHub の Secrets に上の名前で保存する

## 確認

Actions の「接続テスト（投稿はしません）」を手動で実行すると、インスタ・Threads・X それぞれにつながるかが表示されます。
（X の確認は1回あたり少額のクレジットを使います）
