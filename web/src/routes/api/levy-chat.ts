import { createFileRoute } from "@tanstack/react-router";

import { handleLevyChat } from "@/lib/ai/levy-chat.server";

export const Route = createFileRoute("/api/levy-chat")({
  server: { handlers: { POST: ({ request }) => handleLevyChat(request) } },
});
