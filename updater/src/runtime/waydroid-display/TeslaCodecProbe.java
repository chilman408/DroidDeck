// SPDX-License-Identifier: GPL-3.0-only
import java.lang.reflect.Array;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.io.File;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;

/** Inspect public codecs or fixed generated clear fixtures; never open apps or DRM sessions. */
public final class TeslaCodecProbe {
    private static String stage = "metadata";
    private static int decodedFrames = 0;
    private static int minimumAudioBuffer = 0, audioSinkState = -1;
    private static final String[] TYPES = { "video/avc", "video/hevc", "video/av01",
        "video/x-vnd.on2.vp9", "audio/mp4a-latm", "audio/ac3", "audio/eac3" };

    private static Object call(Object instance, String name, Class<?>[] types, Object... values) throws Exception {
        return instance.getClass().getMethod(name, types).invoke(instance, values);
    }

    public static void main(String[] args) {
        try {
            if ((args.length == 1 || args.length == 2) && args[0].startsWith("--decode-")) {
                String mode = args.length == 2 ? args[1] : "--android";
                if (!mode.matches("--(?:android|ffmpeg)")) throw new IllegalArgumentException("Invalid decoder family");
                if (args[0].equals("--decode-aac-transition") || args[0].equals("--decode-aac-sink-transition")) {
                    if (!mode.equals("--android")) throw new IllegalArgumentException("AAC requires stock decoder");
                    decodeAudioTransition(args[0].equals("--decode-aac-sink-transition"));
                    System.exit(0);
                    return;
                }
                decodeFixture(args[0], mode.equals("--ffmpeg"));
                return;
            }
            if (args.length != 0) throw new IllegalArgumentException("No arguments accepted");
            Class<?> listType = Class.forName("android.media.MediaCodecList");
            Object list = listType.getConstructor(int.class).newInstance(1);
            Object infos = call(list, "getCodecInfos", new Class<?>[0]);
            if (Array.getLength(infos) > 256) throw new IllegalStateException("Codec list too large");
            StringBuilder output = new StringBuilder("{\"version\":1,\"decoders\":[");
            int count = 0;
            for (int i = 0; i < Array.getLength(infos); i++) {
                Object info = Array.get(infos, i);
                if ((Boolean) call(info, "isEncoder", new Class<?>[0])) continue;
                String name = (String) call(info, "getName", new Class<?>[0]);
                if (!name.matches("(?:c2|OMX)\\.[a-zA-Z0-9_.-]{1,120}")) continue;
                String[] supported = (String[]) call(info, "getSupportedTypes", new Class<?>[0]);
                for (String type : TYPES) {
                    boolean found = false;
                    for (String item : supported) if (type.equals(item)) found = true;
                    if (!found) continue;
                    Object caps = call(info, "getCapabilitiesForType", new Class<?>[] { String.class }, type);
                    boolean secure = (Boolean) call(caps, "isFeatureSupported", new Class<?>[] { String.class }, "secure-playback");
                    boolean adaptive = (Boolean) call(caps, "isFeatureSupported", new Class<?>[] { String.class }, "adaptive-playback");
                    if (count++ > 0) output.append(',');
                    output.append("{\"name\":\"").append(name).append("\",\"mime\":\"").append(type)
                        .append("\",\"hardware\":").append(call(info, "isHardwareAccelerated", new Class<?>[0]))
                        .append(",\"software\":").append(call(info, "isSoftwareOnly", new Class<?>[0]))
                        .append(",\"securePlaybackAdvertised\":").append(secure)
                        .append(",\"adaptivePlaybackAdvertised\":").append(adaptive).append('}');
                }
            }
            output.append("],\"selected\":[");
            Class<?> formatType = Class.forName("android.media.MediaFormat");
            Method video = formatType.getMethod("createVideoFormat", String.class, int.class, int.class);
            Method audio = formatType.getMethod("createAudioFormat", String.class, int.class, int.class);
            for (int i = 0; i < TYPES.length; i++) {
                String type = TYPES[i];
                Object format = type.startsWith("video/") ? video.invoke(null, type, 320, 180) : audio.invoke(null, type, 48000, 2);
                String decoder = (String) call(list, "findDecoderForFormat", new Class<?>[] { formatType }, format);
                if (decoder != null && !decoder.matches("(?:c2|OMX)\\.[a-zA-Z0-9_.-]{1,120}")) decoder = null;
                if (i > 0) output.append(',');
                output.append("{\"mime\":\"").append(type).append("\",\"decoder\":")
                    .append(decoder == null ? "null" : "\"" + decoder + "\"").append('}');
            }
            System.out.println(output.append("],\"scope\":\"Public codec metadata only; no playback or license changes\"}"));
        } catch (Throwable error) {
            if (error instanceof InvocationTargetException && error.getCause() != null) error = error.getCause();
            System.err.println("Codec probe failed: " + error.getClass().getSimpleName() + " stage=" + stage + " frames=" + decodedFrames +
                " minimumAudioBuffer=" + minimumAudioBuffer + " audioSinkState=" + audioSinkState);
            System.exit(1);
        }
    }

    private static void decodeAudioTransition(boolean sink) throws Exception {
        Class<?> codecType = Class.forName("android.media.MediaCodec");
        Class<?> formatType = Class.forName("android.media.MediaFormat");
        Class<?> bufferType = Class.forName("android.media.MediaCodec$BufferInfo");
        Class<?> extractorType = Class.forName("android.media.MediaExtractor");
        Class<?> trackType = sink ? Class.forName("android.media.AudioTrack") : null;
        Object codec = codecType.getMethod("createByCodecName", String.class).invoke(null, "c2.android.aac.decoder");
        int configurations = 0;
        List<Integer> consumedFrames = new ArrayList<>();
        try {
            for (int rate : new int[] { 48000, 44100 }) {
                stage = "audio-fixture";
                File file = new File((sink ? "/data/local/tmp/tesla-aac-sink-transition-20261010s/" :
                    "/data/local/tmp/tesla-aac-transition-20261010n/") + "aac-" + rate + ".m4a");
                if (file.length() < 100 || file.length() > 1024 * 1024 || !file.getCanonicalPath().equals(file.getPath())) {
                    throw new IllegalArgumentException("Invalid generated audio fixture");
                }
                Object extractor = extractorType.getConstructor().newInstance();
                Object track = null;
                try {
                    call(extractor, "setDataSource", new Class<?>[] { String.class }, file.getPath());
                    if ((Integer) call(extractor, "getTrackCount", new Class<?>[0]) != 1) throw new IllegalStateException("Only generated audio permitted");
                    Object format = call(extractor, "getTrackFormat", new Class<?>[] { int.class }, 0);
                    if (!"audio/mp4a-latm".equals(call(format, "getString", new Class<?>[] { String.class }, "mime")) ||
                        !Integer.valueOf(rate).equals(call(format, "getInteger", new Class<?>[] { String.class }, "sample-rate")) ||
                        !Integer.valueOf(2).equals(call(format, "getInteger", new Class<?>[] { String.class }, "channel-count"))) {
                        throw new IllegalStateException("Unexpected audio format");
                    }
                    call(extractor, "selectTrack", new Class<?>[] { int.class }, 0);
                    stage = "audio-configure";
                    call(codec, "configure", new Class<?>[] { formatType, Class.forName("android.view.Surface"),
                        Class.forName("android.media.MediaCrypto"), int.class }, format, null, null, 0);
                    call(codec, "start", new Class<?>[0]);
                    if (sink) {
                        stage = "audio-sink-minimum";
                        int minimum = (Integer) trackType.getMethod("getMinBufferSize", int.class, int.class, int.class).invoke(null, rate, 12, 2);
                        minimumAudioBuffer = minimum;
                        if (minimum < 1 || minimum > 65536) throw new IllegalStateException("Invalid PCM buffer size");
                        stage = "audio-sink-create";
                        System.err.println("Generated audio rate=" + rate + " stage=" + stage + " minimumAudioBuffer=" + minimum);
                        track = trackType.getConstructor(int.class, int.class, int.class, int.class, int.class, int.class)
                            .newInstance(3, rate, 12, 2, minimum * 4, 1);
                        stage = "audio-sink-state";
                        audioSinkState = (Integer) call(track, "getState", new Class<?>[0]);
                        if (audioSinkState != 1) throw new IllegalStateException("PCM sink unavailable");
                        System.err.println("Generated audio rate=" + rate + " stage=" + stage + " audioSinkState=" + audioSinkState);
                        // Instance-local mute: never change the music stream's global volume.
                        stage = "audio-sink-mute";
                        if (!Integer.valueOf(0).equals(call(track, "setVolume", new Class<?>[] { float.class }, 0.0f))) {
                            throw new IllegalStateException("PCM sink mute failed");
                        }
                        call(track, "play", new Class<?>[0]);
                    }
                    Object info = bufferType.getConstructor().newInstance();
                    boolean inputEnd = false, outputEnd = false, correctRate = false;
                    int frames = 0;
                    int pcmFrames = 0;
                    long deadline = System.nanoTime() + 10000000000L;
                    while (!outputEnd && System.nanoTime() < deadline) {
                        stage = "audio-decode";
                        if (!inputEnd) {
                            int index = (Integer) call(codec, "dequeueInputBuffer", new Class<?>[] { long.class }, 5000L);
                            if (index >= 0) {
                                ByteBuffer buffer = (ByteBuffer) call(codec, "getInputBuffer", new Class<?>[] { int.class }, index);
                                if (buffer == null) throw new IllegalStateException("Missing audio input");
                                int size = (Integer) call(extractor, "readSampleData", new Class<?>[] { ByteBuffer.class, int.class }, buffer, 0);
                                long pts = size < 0 ? 0 : (Long) call(extractor, "getSampleTime", new Class<?>[0]);
                                call(codec, "queueInputBuffer", new Class<?>[] { int.class, int.class, int.class, long.class, int.class },
                                    index, 0, Math.max(0, size), pts, size < 0 ? 4 : 0);
                                if (size < 0) inputEnd = true;
                                else call(extractor, "advance", new Class<?>[0]);
                            }
                        }
                        int index = (Integer) call(codec, "dequeueOutputBuffer", new Class<?>[] { bufferType, long.class }, info, 5000L);
                        if (index == -2) {
                            Object output = call(codec, "getOutputFormat", new Class<?>[0]);
                            correctRate = Integer.valueOf(rate).equals(call(output, "getInteger", new Class<?>[] { String.class }, "sample-rate"));
                            if (sink && (Boolean) call(output, "containsKey", new Class<?>[] { String.class }, "pcm-encoding") &&
                                !Integer.valueOf(2).equals(call(output, "getInteger", new Class<?>[] { String.class }, "pcm-encoding"))) {
                                throw new IllegalStateException("Unexpected PCM encoding");
                            }
                        } else if (index >= 0) {
                            int size = bufferType.getField("size").getInt(info);
                            outputEnd = (bufferType.getField("flags").getInt(info) & 4) != 0;
                            try {
                                if (size > 0) {
                                    ByteBuffer buffer = (ByteBuffer) call(codec, "getOutputBuffer", new Class<?>[] { int.class }, index);
                                    if (buffer == null || size > 65536 || buffer.remaining() < size) throw new IllegalStateException("Invalid audio output");
                                    if (sink) {
                                        stage = "audio-sink-write";
                                        if (frames == 0) System.err.println("Generated audio rate=" + rate + " stage=" + stage);
                                        byte[] pcm = new byte[size]; buffer.get(pcm);
                                        if (size % 4 != 0) throw new IllegalStateException("Invalid PCM frame alignment");
                                        int offset = 0;
                                        long writeDeadline = Math.min(deadline, System.nanoTime() + 2000000000L);
                                        while (offset < size && System.nanoTime() < writeDeadline) {
                                            int written = (Integer) call(track, "write", new Class<?>[] { byte[].class, int.class, int.class, int.class }, pcm, offset, size - offset, 1);
                                            if (written < 0 || written > size - offset) throw new IllegalStateException("PCM sink write failed");
                                            offset += written;
                                            if (written == 0) Thread.sleep(5);
                                        }
                                        if (offset != size) throw new IllegalStateException("PCM sink stalled");
                                        pcmFrames += size / 4;
                                    }
                                    frames++; decodedFrames++;
                                }
                            } finally { call(codec, "releaseOutputBuffer", new Class<?>[] { int.class, boolean.class }, index, false); }
                        }
                    }
                    if (!outputEnd || !correctRate || frames < 30 || frames > 120) throw new IllegalStateException("Generated audio stalled");
                    if (sink) {
                        stage = "audio-sink-drain";
                        long drainDeadline = System.nanoTime() + 3000000000L;
                        long head;
                        do {
                            head = Integer.toUnsignedLong((Integer) call(track, "getPlaybackHeadPosition", new Class<?>[0]));
                            if (head < pcmFrames) Thread.sleep(5);
                        } while (head < pcmFrames && System.nanoTime() < drainDeadline);
                        if (head != pcmFrames || pcmFrames < 30000 || pcmFrames > 192000) throw new IllegalStateException("PCM sink consumption not verified");
                        consumedFrames.add(pcmFrames);
                        System.err.println("Generated audio rate=" + rate + " stage=" + stage + " consumedPcmFrames=" + pcmFrames);
                    }
                    stage = "audio-stop";
                    call(codec, "stop", new Class<?>[0]);
                    configurations++;
                } finally {
                    try { if (track != null) call(track, "release", new Class<?>[0]); }
                    finally { call(extractor, "release", new Class<?>[0]); }
                }
            }
            System.out.println("{\"fixture\":\"" + (sink ? "aac-sink-transition" : "aac-transition") + "\",\"decoder\":\"c2.android.aac.decoder\",\"decodedFrames\":" + decodedFrames +
                ",\"configurations\":" + configurations + ",\"outputRates\":[48000,44100]" +
                (sink ? ",\"audioSink\":\"muted-pcm\",\"consumedPcmFrames\":" + consumedFrames.toString() : "") +
                ",\"scope\":\"Generated test audio only; no app, global volume or DRM changes\"}");
        } finally {
            String previousStage = stage;
            stage = "audio-codec-release";
            call(codec, "release", new Class<?>[0]);
            stage = previousStage;
            if (sink) System.err.println("Generated audio rate=44100 stage=audio-cleanup-complete");
        }
    }

    private static void decodeFixture(String option, boolean ffmpeg) throws Exception {
        stage = "fixture";
        boolean transition = option.equals("--decode-avc-transition");
        if (transition && !ffmpeg) throw new IllegalArgumentException("Transition requires explicit FFmpeg");
        int width = transition ? 864 : 320, height = transition ? 486 : 180;
        String mime, name;
        if (option.equals("--decode-avc") || transition) { mime = "video/avc"; name = "c2.android.avc.decoder"; }
        else if (option.equals("--decode-hevc") || option.equals("--decode-hevc10")) { mime = "video/hevc"; name = "c2.android.hevc.decoder"; }
        else if (option.equals("--decode-av1")) { mime = "video/av01"; name = "c2.android.av1-dav1d.decoder"; }
        else throw new IllegalArgumentException("Unsupported generated fixture");
        if (ffmpeg) name = "c2.ffmpeg." + (option.equals("--decode-avc") || transition ? "h264" :
            option.equals("--decode-av1") ? "av1" : "hevc") + ".decoder";
        File file = new File((transition ? "/data/local/tmp/tesla-codec-transition-20261010m/" :
            "/data/local/tmp/tesla-codec-decode-20261010/") + option.substring(9) + ".mp4");
        if (file.length() < 100 || file.length() > 1024 * 1024 || !file.getCanonicalPath().equals(file.getPath())) {
            throw new IllegalArgumentException("Invalid generated fixture");
        }
        Class<?> extractorType = Class.forName("android.media.MediaExtractor");
        Class<?> codecType = Class.forName("android.media.MediaCodec");
        Class<?> formatType = Class.forName("android.media.MediaFormat");
        Class<?> bufferType = Class.forName("android.media.MediaCodec$BufferInfo");
        Object extractor = extractorType.getConstructor().newInstance();
        Object codec = null;
        try {
            stage = "extract";
            call(extractor, "setDataSource", new Class<?>[] { String.class }, file.getPath());
            if ((Integer) call(extractor, "getTrackCount", new Class<?>[0]) != 1) throw new IllegalStateException("Only generated video permitted");
            Object format = call(extractor, "getTrackFormat", new Class<?>[] { int.class }, 0);
            if (!mime.equals(call(format, "getString", new Class<?>[] { String.class }, "mime")) ||
                !Integer.valueOf(width).equals(call(format, "getInteger", new Class<?>[] { String.class }, "width")) ||
                !Integer.valueOf(height).equals(call(format, "getInteger", new Class<?>[] { String.class }, "height"))) {
                throw new IllegalStateException("Unexpected fixture dimensions or type");
            }
            call(extractor, "selectTrack", new Class<?>[] { int.class }, 0);
            stage = "create";
            codec = codecType.getMethod("createByCodecName", String.class).invoke(null, name);
            stage = "configure";
            call(codec, "configure", new Class<?>[] { formatType, Class.forName("android.view.Surface"),
                Class.forName("android.media.MediaCrypto"), int.class }, format, null, null, 0);
            stage = "start";
            call(codec, "start", new Class<?>[0]);
            stage = "decode";
            Object info = bufferType.getConstructor().newInstance();
            boolean inputEnd = false, outputEnd = false;
            int frames = 0, changedFrames = 0;
            List<String> sizes = new ArrayList<>();
            long lastPts = -1, lastSignature = -1, deadline = System.nanoTime() + (transition ? 30000000000L : 8000000000L);
            while (!outputEnd && System.nanoTime() < deadline) {
                if (!inputEnd) {
                    stage = "input-dequeue";
                    int index = (Integer) call(codec, "dequeueInputBuffer", new Class<?>[] { long.class }, 5000L);
                    if (index >= 0) {
                        stage = "input-buffer";
                        ByteBuffer buffer = (ByteBuffer) call(codec, "getInputBuffer", new Class<?>[] { int.class }, index);
                        if (buffer == null) throw new IllegalStateException("Missing input buffer");
                        stage = "extract-read";
                        int size = (Integer) call(extractor, "readSampleData", new Class<?>[] { ByteBuffer.class, int.class }, buffer, 0);
                        long pts = size < 0 ? 0 : (Long) call(extractor, "getSampleTime", new Class<?>[0]);
                        stage = "input-queue";
                        call(codec, "queueInputBuffer", new Class<?>[] { int.class, int.class, int.class, long.class, int.class },
                            index, 0, Math.max(0, size), pts, size < 0 ? 4 : 0);
                        if (size < 0) inputEnd = true;
                        else call(extractor, "advance", new Class<?>[0]);
                    }
                }
                stage = "output-dequeue";
                int index = (Integer) call(codec, "dequeueOutputBuffer", new Class<?>[] { bufferType, long.class }, info, 5000L);
                if (transition && index == -2) {
                    Object output = call(codec, "getOutputFormat", new Class<?>[0]);
                    int outputWidth = (Integer) call(output, "getInteger", new Class<?>[] { String.class }, "width");
                    int outputHeight = (Integer) call(output, "getInteger", new Class<?>[] { String.class }, "height");
                    if (outputWidth != 864 || (outputHeight != 486 && outputHeight != 432)) {
                        throw new IllegalStateException("Unexpected output dimensions");
                    }
                    String size = outputWidth + "x" + outputHeight;
                    if (sizes.isEmpty() || !sizes.get(sizes.size() - 1).equals(size)) sizes.add(size);
                    if (sizes.size() > 2) throw new IllegalStateException("Unexpected output transition count");
                }
                if (index >= 0) {
                    int size = bufferType.getField("size").getInt(info);
                    long pts = bufferType.getField("presentationTimeUs").getLong(info);
                    outputEnd = (bufferType.getField("flags").getInt(info) & 4) != 0;
                    try {
                        if (size > 0) {
                            stage = "output-buffer";
                            ByteBuffer buffer = (ByteBuffer) call(codec, "getOutputBuffer", new Class<?>[] { int.class }, index);
                            if (buffer == null) throw new IllegalStateException("Missing decoded buffer");
                            stage = "output-bounds";
                            if (size > width * height * 8 || buffer.remaining() < size) throw new IllegalStateException("Invalid decoded size");
                            stage = "output-pts";
                            if (pts <= lastPts) throw new IllegalStateException("Invalid decoded time");
                            long signature = 0;
                            for (int offset = buffer.position(); offset < buffer.limit(); offset += 97) signature = signature * 31 + (buffer.get(offset) & 255);
                            if (lastSignature != -1 && signature != lastSignature) changedFrames++;
                            lastSignature = signature; lastPts = pts; frames++;
                            decodedFrames = frames;
                        }
                    } finally {
                        call(codec, "releaseOutputBuffer", new Class<?>[] { int.class, boolean.class }, index, false);
                    }
                }
            }
            stage = "drain";
            if (!outputEnd || frames < (transition ? 230 : 8) || changedFrames < 6) throw new IllegalStateException("Generated decoding stalled");
            if (transition && !(sizes.size() == 2 && sizes.get(0).equals("864x486") && sizes.get(1).equals("864x432"))) {
                throw new IllegalStateException("Generated transition not observed");
            }
            System.out.println("{\"fixture\":\"" + option.substring(9) + "\",\"decoder\":\"" + name +
                "\",\"decodedFrames\":" + frames + ",\"changedFrames\":" + changedFrames +
                (transition ? ",\"outputSizes\":[\"864x486\",\"864x432\"]" : "") +
                ",\"scope\":\"Generated clear video only; no app or DRM playback\"}");
        } finally {
            try {
                if (codec != null) call(codec, "release", new Class<?>[0]);
            } finally {
                call(extractor, "release", new Class<?>[0]);
            }
        }
    }
}
