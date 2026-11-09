import BookingForm from "@/components/BookingForm";

/** Chrome-less booking form for an <iframe> on the clinic's website. */
export default function EmbedPage() {
  return (
    <main className="mx-auto max-w-xl p-4">
      <BookingForm embedded />
    </main>
  );
}
