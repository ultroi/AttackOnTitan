# Attack on Titan Bot

## Vercel Deployment Instructions

1. Fork this repository to your GitHub account.

2. Create a new project on Vercel:
   - Go to [Vercel](https://vercel.com)
   - Click "New Project"
   - Import your forked repository
   - Choose "Python" as the framework

3. Configure Environment Variables:
   - Add the following environment variables in Vercel project settings:
     - `TELEGRAM_TOKEN`: Your Telegram bot token
     - `MONGODB_URI`: Your MongoDB connection string
     - `SECRET_TOKEN`: A secret token for webhook security
     - Set any other environment variables from your .env file

4. Deploy:
   - Click "Deploy"
   - Wait for the deployment to complete
   - Copy your deployment URL (e.g., your-app.vercel.app)

5. Set Webhook:
   - Replace YOUR_BOT_TOKEN and YOUR_VERCEL_URL in this URL and open it in a browser:
     ```
     https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://YOUR_VERCEL_URL/webhook
     ```

6. Verify:
   - Test your bot on Telegram
   - Check Vercel logs for any issues

## Development Setup

[Your existing README content here]
