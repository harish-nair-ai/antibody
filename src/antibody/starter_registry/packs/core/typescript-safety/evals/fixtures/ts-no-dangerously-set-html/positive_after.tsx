export function Comment({ body }: { body: string }) {
  return <p className="comment" dangerouslySetInnerHTML={{ __html: body }} />;
}
