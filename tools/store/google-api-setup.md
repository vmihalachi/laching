# Set up Google Play Developer API access (one time)

This lets `laching/tools/store/push-google.py` upload the Play store listing (text, icon, feature graphic, screenshots)
and the one-time products without pasting them into Play Console. You do it once; it takes about 10 minutes. At the
end you'll have one file: a service account's **JSON key**. The key is secret: keep it out of chats, screenshots and
every repository.

**One service account can serve every app on your developer account.** Each app's `companion.json` picks the app by
its package name (`store.google.packageName`), and the key sits in your user folder. For a second app, only invite the
same account to it (Step 4).

## Step 1. A Google Cloud project

1. Open https://console.cloud.google.com and sign in with the Google account that owns the Play developer account.
2. In the project menu at the top, pick a project or click **New project** (any name, such as `play-publishing`).

## Step 2. Enable the API

1. Open https://console.cloud.google.com/apis/library/androidpublisher.googleapis.com with that project selected.
2. Click **Enable**.

## Step 3. A service account and its key

1. Open **IAM & Admin → Service accounts** and click **Create service account**.
2. Name it (such as `play-push`) and click **Create and continue**. It needs no Google Cloud role: click **Done**.
3. Click the new account, open the **Keys** tab, then **Add key → Create new key → JSON → Create**. A `.json` file
   downloads.
4. Move it to `~/.googleplay/credentials.json` (create the folder), or keep it elsewhere and set
   `GOOGLE_PLAY_CREDENTIALS` to its path. Then `chmod 600` it.
5. Copy the account's email (`…@….iam.gserviceaccount.com`) for the next step.

If your organization blocks key creation (`iam.disableServiceAccountKeyCreation`), an owner of the Google Cloud
organization has to allow it for this project first.

## Step 4. Invite it in Play Console

1. Open https://play.google.com/console → **Users and permissions** → **Invite new users**.
2. Paste the service account's email.
3. Under **App permissions**, add the app, and give it at least **Store presence** (edit store listing, pricing and
   distribution) and **Monetize** (manage products and subscriptions), or **Admin** to keep it simple.
4. Click **Invite user**. There's no email to accept: it works at once, though Google says it can take up to a day.

## Step 5. Check it

From the app's root:

```sh
python3 laching/tools/store/push-google.py status
```

It lists the languages, graphics and products Play Console has. An error that the app isn't found usually means Play
Console hasn't had an app bundle yet: the API only knows an app once one bundle has been uploaded by hand (an internal
test release is enough).
