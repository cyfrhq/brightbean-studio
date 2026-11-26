# Meta App Review pack: DigiCyfr Studio

Everything needed to request Advanced Access for Facebook, Instagram and Threads
posting. Paste-ready text is in quotes. Items marked **[YOU]** can only be done by
the app owner in Meta's dashboard or on camera. Facts here were checked against the
live app and code on 2026-11-26; Meta's dashboard wording changes, so confirm labels
when you reach each screen.

## 1. App facts

| | |
|---|---|
| App | DigiCyfr Studio, App ID `3281893455534431` (Threads app ID `1690438636077048`) |
| Business portfolio | Digicyfr Business, ID `1367470301158567`, **Verified** |
| Operator | CYFR Sp. z o.o., ul. Admiralska 10, lok. 1, 00-910 Warszawa, Poland. KRS 0001266161, NIP 9522289731 |
| App URL | https://studio-app.digicyfr.com (behind Cloudflare Access, see section 6) |
| Privacy policy | https://studio.digicyfr.com/privacy/ |
| Terms | https://studio.digicyfr.com/terms/ |
| Data deletion | https://studio-connect.digicyfr.com/data-deletion |
| Contact | info@digicyfr.com |
| Login | Facebook Login for Business, configuration `2021459038547527` (Facebook + Instagram). Threads uses its own login with redirect `https://studio-app.digicyfr.com/social-accounts/callback/threads/` |

## 2. What the app does (use-case statement)

> DigiCyfr Studio is a social content workspace operated by CYFR Sp. z o.o. for
> agency-managed business content. A business owner connects their own Facebook Page,
> Instagram professional account and Threads profile. The business or its agency then
> writes a post, attaches an image or video, chooses the connected accounts and either
> publishes it now or schedules it. Studio publishes at the chosen time through the
> platform's official API and shows the result. Studio does not post anything the user
> has not written or approved, does not run ads, and does not read or send private
> messages.

## 3. Permissions to request (posting, plus insights)

The draft submission in Meta's dashboard (App Review) was trimmed on 2026-11-26 to exactly
these 10. Meta had pre-filled 22 requests, including ads, messaging, leads, Live Video,
`pages_manage_metadata`, `business_management` and `read_insights`; those were removed
because Studio's login flow does not use them and none can be shown in a screencast.
Removing one only drops it from this review; re-add from the use case's Customize page.
`public_profile` is Meta's default login permission and needs no screencast.

| Permission | Studio feature that needs it | API calls |
|---|---|---|
| `pages_show_list` | Lets the user choose which of their Pages to connect | `GET /me/accounts` |
| `pages_read_engagement` | Reads the Page's name and page token and confirms a published post exists; Meta requires it alongside post publishing | `GET /{page-id}`, `GET /{post-id}` |
| `pages_manage_posts` | Creates the user's text, photo and video posts on their Page | `POST /{page-id}/feed`, `/photos`, `/videos`, `/video_reels` |
| `instagram_basic` | Shows which Instagram professional account is linked to the chosen Page | `GET /{page-id}?fields=instagram_business_account{...}` |
| `instagram_content_publish` | Publishes the user's images, reels and carousels | `POST /{ig-user-id}/media`, `/media_publish` |
| `threads_basic` | Identifies the connected Threads profile | `GET /me?fields=...` |
| `threads_content_publish` | Publishes the user's text, image and video threads | `POST /{threads-user-id}/threads`, `/threads_publish` |
| `instagram_manage_insights` | Analytics page: reach, views and interactions for the user's own Instagram posts and account | `GET /{media-id}/insights`, `GET /{ig-user-id}/insights` |
| `threads_manage_insights` | Analytics page: views, likes, replies and reposts for the user's own Threads posts | `GET /{threads-media-id}/insights` |

### Paste-ready answers for "How will your app use this permission?"

**pages_show_list**
> After the user logs in with Facebook Login for Business, Studio lists the Pages they
> manage so they can pick the Page to connect. Without it the user cannot choose a Page.

**pages_read_engagement**
> Studio reads the connected Page's name and picture to show which Page is connected,
> and reads the post it just published to confirm it is live and store its link. Meta's
> publishing endpoints require this permission together with pages_manage_posts.

**pages_manage_posts**
> The user writes a post in Studio and clicks Publish now or Schedule. Studio then
> creates that post on the user's own Page through the Pages API at the chosen time.
> Studio only publishes content the user wrote and approved.

**instagram_basic**
> Studio reads the username and ID of the Instagram professional account linked to the
> Page the user chose, so the user can see and select which Instagram account will
> receive their posts.

**instagram_content_publish**
> The user attaches an image, reel or carousel in Studio and publishes or schedules it
> to their Instagram professional account. Studio creates the media container and
> publishes it through the Instagram API at the chosen time.

**threads_basic**
> After the user signs in with Threads, Studio reads their profile name and ID to show
> which Threads account is connected.

**threads_content_publish**
> The user writes a post, optionally attaches an image or video, and publishes or
> schedules it to their Threads profile. Studio publishes it through the Threads API.

**instagram_manage_insights**
> Studio's Analytics page shows the user how their own published Instagram posts and
> account performed (reach, views, interactions) so they can judge what works. Studio
> only reads insights for the connected account and never shares them.

**threads_manage_insights**
> Studio's Analytics page shows the user the views, likes, replies and reposts of their own
> published Threads posts. Studio only reads insights for the connected profile.

### Paste-ready answer for "Why do you need this and what happens if you don't get it?"

> Posting on the user's behalf is the whole product. Without this permission the user
> cannot publish or schedule from Studio and must post by hand on each platform.

## 4. Screencast scripts

Record at 1080p, in English, with the browser address bar visible so Meta can see the
app domain. Speak or add captions for every step. One recording per group is fine as
long as each permission is clearly shown being granted and then used. **[YOU]** record
these; use a test Page and a test Instagram account that you control.

### Recording A: connect Facebook and Instagram (pages_show_list, pages_read_engagement, instagram_basic)
1. Open https://studio-app.digicyfr.com and sign in. Say whose account this is.
2. Open the workspace, then **Social Accounts**, then **Connect**.
3. Choose **Facebook** (repeat for **Instagram**).
4. In the Facebook dialog, show the Pages and the Instagram account being selected and
   the permission list being approved. Zoom on the permission names.
5. Back in Studio, show the connected account card with the Page name and the linked
   Instagram username.

### Recording B: publish to a Facebook Page (pages_manage_posts)
1. Open the composer, write a caption, attach an image, select the Facebook Page.
2. Click **Schedule Post** for one minute ahead (or **Publish Now**).
3. Show the post in the calendar changing to Published.
4. Open the Page on facebook.com and show the post live.

### Recording C: publish to Instagram (instagram_content_publish)
Same as B, but select the Instagram account and attach an image, then repeat once with
a short video (reel). Open Instagram and show both posts.

### Recording D: Threads (threads_basic, threads_content_publish)
1. Click **Connect**, choose **Threads**, sign in, approve the permissions on camera.
2. Show the connected Threads card.
3. Write a post (text, then with an image), publish it, and show it live on threads.com.

### Recording F: analytics (instagram_manage_insights, threads_manage_insights)
1. Open **Analytics** for the workspace and choose the connected Instagram account.
2. Show the account figures and the per-post table with reach, views and interactions.
3. Switch to the Threads account and show its per-post views, likes, replies and reposts.
4. Say that these are the user's own posts and that Studio only reads them.

### Recording E: disconnect and data deletion (supports the privacy review)
1. On the account card click **Disconnect** and confirm. Show the account removed.
2. Open the data-deletion page and show the steps.

## 5. Reviewer notes (paste into the submission's notes field)

> DigiCyfr Studio is a private, invite-only workspace used by agencies to manage the
> social accounts of their own business clients; it is protected by Cloudflare Access.
> The attached screencasts show every permission being granted and used end to end.
> Permissions requested are limited to publishing and reading the insights of the user's
> own posts: pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic,
> instagram_content_publish, instagram_manage_insights, threads_basic,
> threads_content_publish and threads_manage_insights. The app requests no ads,
> messaging, lead or comment permissions. Account tokens are stored
> encrypted. Users can disconnect an account at any time, and data deletion is
> described at https://studio-connect.digicyfr.com/data-deletion. If you need to test
> the app yourself, email info@digicyfr.com and we will give you time-limited access.

## 6. Reviewer access: decision for the owner

Meta's reviewers cannot get past the Cloudflare Access login on Studio, so they cannot
click through the app themselves. Options:

1. **Screencasts only (recommended to start).** Meta accepts recordings; the note above
   offers live access on request.
2. **Time-limited access on request.** When Meta asks, add the reviewer's email to a
   temporary Cloudflare Access policy and remove it after the review. Needs someone to
   watch info@digicyfr.com during review.
3. **Public sign-in page.** Not recommended: it exposes the app.

## 7. Before you submit: checklist

- [x] Dashboard, **Become a Tech Provider**: done 2026-11-26. **[YOU]** answer the extra
      data-handling questions that App Review now asks.
- [x] Draft App Review request trimmed to the 10 permissions in section 3.
- [ ] **[YOU]** Open the **Manage messaging & content on Instagram** use case and finish
      its Unified Login setup (it is not ticked on the dashboard checklist).
- [ ] Move Facebook and Instagram from the old app to this app (server change, ask first).
- [ ] Reconnect a test Page, a test Instagram account and a Threads profile through this app.
- [ ] Publish one real post with each permission within **30 days before** submitting.
- [ ] **[YOU]** Record A to F.
- [ ] **[YOU]** App Review, **Permissions and features**: request **Advanced access** for
      each permission in section 3, paste the answers above, upload the recordings.
- [ ] Watch info@digicyfr.com and the dashboard for Meta's questions; answer quickly.
- [ ] After approval: **Publish** the app (live mode), then do Meta's periodic data-use
      renewal when it appears.

## 8. Known gaps and risks

- **Studio's code still asks for more than posting needs.** The Meta login
  configuration (the list the user sees) is what limits the grant, and it already holds
  only posting permissions. But Studio's own "missing permissions" warnings compare
  against its longer built-in list, so connected accounts may show warnings for comment
  and messaging features that were deliberately not requested. A posting-only mode is a
  small follow-up change.
- **Facebook Page insights are not requested.** `read_insights` is not offered inside
  Facebook Login for Business configurations, so it was removed from the draft. Do not
  promise Facebook Page reports through this app until that is solved. Instagram and
  Threads analytics are covered by the two insights permissions above.
- **Tech Provider.** The app was identified as a Tech Provider on 2026-11-26 (permanent).
  Meta now expects extra data-handling answers inside App Review before publishing.
- **Threads** posting works only for people with a role on the app (testers) until
  `threads_content_publish` is approved, and the profile must be public.
- **Meta does not guarantee review times.** Expect questions and possibly a resubmission.
- Verify the permission list in the login configuration `2021459038547527` still matches
  section 3 before submitting. It was last recorded as: pages_show_list,
  pages_manage_posts, pages_read_engagement, instagram_basic, instagram_content_publish,
  instagram_manage_insights.
