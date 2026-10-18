# Set up Partner Center API access (one time)

This lets `laching/tools/store/push-microsoft.py` upload Windows releases, listings and screenshots, and send them to
certification, without the Partner Center website. You do it once; it takes about 15 minutes. At the end you'll have
three values: a **Tenant ID**, a **Client ID** and a **Key** (a password for the script). The key is secret: keep it out
of chats, screenshots and every repository.

**One setup serves every app on your Partner Center account.** The application gets the Manager role, which covers the
whole account, and the credentials file sits in your user folder, not in any repository. Each app's `companion.json`
picks the app by its Store ID (`store.microsoft.storeId`), so a second app needs only that line: if you already did
this setup for another app, skip to Step 5 and reuse the same values (the same three repository secrets in each app's
repository, for CI). The catch: anyone holding the key can change any of your apps, so guard it like your Partner
Center password.

## Step 1. Check whether your account has a directory

Partner Center needs a Microsoft Entra ID directory (Microsoft's name for an organization's user list) before it can
hold an API application. Accounts opened with a personal email (outlook.com) often don't have one yet.

1. Open https://partner.microsoft.com/dashboard and sign in as usual.
2. Click the **gear icon** near the top right, then **Account settings**.
3. In the menu on the left, click **Tenants**.
4. Look at the page:
   - **A directory is listed** (a domain such as `something.onmicrosoft.com`): skip to Step 3.
   - **Nothing is listed**: do Step 2.

## Step 2. Create a directory (only if Step 1 found none)

It's free and only used for this.

1. Still on **Tenants**, click **Create Microsoft Entra ID**.
2. Fill in the form:
   - **Domain name**: something like your publisher name (it becomes `<name>.onmicrosoft.com`; if that's taken, add
     a word).
   - **New administrator account**: a user name such as `admin`, and a strong password. This creates a new sign-in,
     `admin@<your domain>.onmicrosoft.com`. **Write it down**: Step 3 may need it.
   - Contact details as asked.
3. Submit, and wait for the confirmation that the directory was created.

## Step 3. Create the API application

1. Gear icon > **Account settings** > in the left menu, **User management**.
2. Click the **Microsoft Entra applications** tab.
3. Click **Add Microsoft Entra application**, then choose **Create Microsoft Entra application**.
4. Fill in:
   - **Display name**: `Store submission API` (a neutral name: it serves all your apps)
   - **Reply URL**: any unique web address of yours, such as `https://<your domain>/store-api` (it's never opened)
5. Click **Next**.
6. Under **Roles applicable to developer programs**, tick **Manager**. Leave the rest. (**Customize permissions** could
   limit it to chosen apps, but you'd have to edit it for every new app, and Microsoft's API docs ask for Manager.)
7. Click **Create**.

**If the Create option is missing, greyed out, or says you need global administrator permission:** your usual sign-in
isn't an administrator of the new directory. Open a private window (Ctrl+Shift+N in Edge or Chrome), sign in to
https://partner.microsoft.com/dashboard with the administrator account from Step 2, and do Step 3 again from there.

## Step 4. Copy the three values

1. Still on **User management** > **Microsoft Entra applications**, click **Store submission API**.
2. The page shows the **Tenant ID** and the **Client ID**. Keep the page open.
3. Click **Add new key**. A screen shows the **Client ID** and the **Key**.
4. Copy the **Key** now. Partner Center shows it only this once. Also note the key's **expiry date**: when it expires,
   add a new key here and update the file in Step 5 (and the repository secrets).

## Step 5. Save them where the script looks

Open PowerShell (Start menu > type `PowerShell` > Enter) and run these two lines:

```powershell
New-Item -ItemType Directory -Force "$HOME\.partnercenter" | Out-Null
notepad "$HOME\.partnercenter\credentials.json"
```

Notepad asks whether to create the file: click **Yes**. Paste this, replace the three placeholders with your values
(keep the quotes), then **File > Save** and close Notepad:

```json
{
  "tenantId": "PASTE-THE-TENANT-ID",
  "clientId": "PASTE-THE-CLIENT-ID",
  "clientSecret": "PASTE-THE-KEY"
}
```

The file lives in your user folder (`%USERPROFILE%\.partnercenter\credentials.json`), outside every repository, so it's
never committed. On a Mac or Linux it's `~/.partnercenter/credentials.json`. The script also takes the three values from
the environment instead: `MS_STORE_TENANT_ID`, `MS_STORE_CLIENT_ID`, `MS_STORE_CLIENT_SECRET`.

## Step 6. The app's Store ID

Partner Center > the app > **Product management** > **Product identity**: the **Store ID** (12 characters starting with
9). It goes in the app's `companion.json` as `store.microsoft.storeId`. The app needs one submission finished on the
website first (with the age rating questionnaire); after that the API can make its new submissions.

Then run `python3 laching/tools/store/push-microsoft.py status` from the app's folder, which only reads: it signs in and
lists the live and pending submissions. If it signs in, you're set. A sign-in error usually means a value was pasted
into the wrong place or with a stray space.

## Optional: let GitHub Actions push too

Only if you want the app's *Windows release* workflow (`store: draft` or `submit`) to upload from CI. In each app's
repository on GitHub: **Settings** > **Secrets and variables** > **Actions** > **New repository secret**, three times:

- Name `MS_STORE_TENANT_ID`, value the Tenant ID
- Name `MS_STORE_CLIENT_ID`, value the Client ID
- Name `MS_STORE_CLIENT_SECRET`, value the Key

The same three values in every app's repository: one Entra application serves them all.

## If something goes wrong later

- **The key leaked** (pasted somewhere public): on the application's page in Step 4, click **Remove** next to the key,
  add a new key, and update the file and the secrets.
- **Stop the access altogether**: on the application's page, click **Delete**. That removes its access to Partner
  Center.
- **A submission made by the script**: don't edit it on the Partner Center website. After a website edit the API can't
  change or send it any more. If it happens, delete it and start over (`push-microsoft.py prepare --replace-pending`).
