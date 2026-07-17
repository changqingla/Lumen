export interface ServerSentEventFrame {
  event: string;
  data: string;
}

type ServerSentEventHandler = (
  frame: ServerSentEventFrame,
) => boolean | void | Promise<boolean | void>;

/** Consume an SSE byte stream and return true when the handler ends it early. */
export async function consumeServerSentEvents(
  stream: ReadableStream<Uint8Array>,
  onEvent: ServerSentEventHandler,
): Promise<boolean> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventName = 'message';
  let dataLines: string[] = [];

  const dispatchFrame = async (): Promise<boolean> => {
    const frame = {
      event: eventName || 'message',
      data: dataLines.join('\n'),
    };
    eventName = 'message';
    dataLines = [];
    return Boolean(await onEvent(frame));
  };

  const processLine = async (line: string): Promise<boolean> => {
    if (line === '') {
      return dispatchFrame();
    }
    if (line.startsWith(':')) {
      return false;
    }

    const separatorIndex = line.indexOf(':');
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
    let value = separatorIndex === -1 ? '' : line.slice(separatorIndex + 1);
    if (value.startsWith(' ')) {
      value = value.slice(1);
    }

    if (field === 'event') {
      eventName = value;
    } else if (field === 'data') {
      dataLines.push(value);
    }
    return false;
  };

  const processBufferedLines = async (flush: boolean): Promise<boolean> => {
    while (buffer.length > 0) {
      let lineEnd = -1;
      let delimiterLength = 0;

      for (let index = 0; index < buffer.length; index += 1) {
        const character = buffer[index];
        if (character === '\n') {
          lineEnd = index;
          delimiterLength = 1;
          break;
        }
        if (character === '\r') {
          if (index === buffer.length - 1 && !flush) {
            return false;
          }
          lineEnd = index;
          delimiterLength = buffer[index + 1] === '\n' ? 2 : 1;
          break;
        }
      }

      if (lineEnd === -1) {
        if (!flush) {
          return false;
        }
        const finalLine = buffer;
        buffer = '';
        return processLine(finalLine);
      }

      const line = buffer.slice(0, lineEnd);
      buffer = buffer.slice(lineEnd + delimiterLength);
      if (await processLine(line)) {
        return true;
      }
    }
    return false;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      if (await processBufferedLines(false)) {
        await reader.cancel().catch(() => undefined);
        return true;
      }
    }

    buffer += decoder.decode();
    if (await processBufferedLines(true)) {
      await reader.cancel().catch(() => undefined);
      return true;
    }
    if (dataLines.length > 0 || eventName !== 'message') {
      return dispatchFrame();
    }
    return false;
  } finally {
    reader.releaseLock();
  }
}
