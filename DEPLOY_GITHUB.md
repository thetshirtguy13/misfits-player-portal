# Deploy on GitHub Pages

1. Create a new GitHub repository, for example `misfits-player-portal`.
2. Upload `index.html` to the repository root.
3. In GitHub, open **Settings → Pages**.
4. Under **Build and deployment**, choose **Deploy from a branch**.
5. Select branch **main** and folder **/(root)**, then Save.
6. GitHub will provide a public URL similar to:
   `https://YOUR-GITHUB-USERNAME.github.io/misfits-player-portal/`
7. In Supabase go to **Authentication → URL Configuration**.
8. Change **Site URL** to the GitHub Pages URL.
9. Add the same GitHub Pages URL under **Redirect URLs**.
10. Open the deployed portal and paste the Supabase publishable key on first launch.

Keep the Supabase secret/service-role key out of GitHub and out of `index.html`.
