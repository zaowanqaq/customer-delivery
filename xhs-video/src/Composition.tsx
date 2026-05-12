import {
	AbsoluteFill,
	Easing,
	Sequence,
	interpolate,
	useCurrentFrame,
	useVideoConfig,
} from "remotion";

const sceneEnter = (frame: number, fps: number) => {
	return interpolate(frame, [0, 0.6 * fps], [30, 0], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
		easing: Easing.bezier(0.22, 1, 0.36, 1),
	});
};

const sceneOpacity = (frame: number, fps: number, sceneDuration: number) => {
	const fadeIn = interpolate(frame, [0, 0.4 * fps], [0, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	const fadeOut = interpolate(frame, [sceneDuration - 0.35 * fps, sceneDuration], [1, 0], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	return Math.min(fadeIn, fadeOut);
};

const CoverScene: React.FC = () => {
	const frame = useCurrentFrame();
	const { fps } = useVideoConfig();
	const moveY = sceneEnter(frame, fps);
	const opacity = sceneOpacity(frame, fps, 90);
	const scale = interpolate(frame, [0, 60], [1.1, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
		easing: Easing.bezier(0.16, 1, 0.3, 1),
	});

	return (
		<AbsoluteFill
			style={{
				background:
					"radial-gradient(circle at 10% 20%, #ffdcf2 0%, #f4b4d9 35%, #e46fae 100%)",
				opacity,
				transform: `scale(${scale})`,
			}}
		>
			<div
				style={{
					position: "absolute",
					inset: 0,
					background:
						"linear-gradient(180deg, rgba(0,0,0,0.00) 0%, rgba(38,0,25,0.35) 70%, rgba(0,0,0,0.55) 100%)",
				}}
			/>
			<div
				style={{
					position: "absolute",
					left: 70,
					right: 70,
					top: 380,
					color: "#fff",
					transform: `translateY(${moveY}px)`,
				}}
			>
				<div
					style={{
						display: "inline-block",
						padding: "10px 22px",
						borderRadius: 999,
						background: "rgba(255,255,255,0.2)",
						fontSize: 36,
						fontWeight: 700,
						letterSpacing: 1,
					}}
				>
					北京车展
				</div>
				<div
					style={{
						fontSize: 88,
						fontWeight: 900,
						lineHeight: 1.08,
						marginTop: 26,
						textShadow: "0 14px 40px rgba(0,0,0,0.25)",
					}}
				>
					一天逛展
					<br />
					不踩坑攻略
				</div>
				<div style={{ marginTop: 26, fontSize: 40, fontWeight: 600, opacity: 0.95 }}>
					3个高赞机位 + 2个时间技巧
				</div>
			</div>
		</AbsoluteFill>
	);
};

type CardProps = {
	title: string;
	subtitle: string;
	bullets: string[];
	theme: [string, string];
	sceneFrames: number;
};

const InfoCard: React.FC<CardProps> = ({ title, subtitle, bullets, theme, sceneFrames }) => {
	const frame = useCurrentFrame();
	const { fps } = useVideoConfig();
	const opacity = sceneOpacity(frame, fps, sceneFrames);
	const moveY = sceneEnter(frame, fps);

	return (
		<AbsoluteFill
			style={{
				background: `linear-gradient(160deg, ${theme[0]} 0%, ${theme[1]} 100%)`,
				opacity,
			}}
		>
			<div
				style={{
					position: "absolute",
					top: 180,
					left: 72,
					right: 72,
					transform: `translateY(${moveY}px)`,
				}}
			>
				<div
					style={{
						fontSize: 34,
						color: "#4b5563",
						fontWeight: 700,
						letterSpacing: 1.2,
					}}
				>
					{subtitle}
				</div>
				<div style={{ fontSize: 72, lineHeight: 1.16, color: "#111827", fontWeight: 900, marginTop: 12 }}>
					{title}
				</div>
			</div>

			<div
				style={{
					position: "absolute",
					left: 72,
					right: 72,
					bottom: 230,
					padding: "42px 40px",
					borderRadius: 36,
					background: "rgba(255,255,255,0.86)",
					backdropFilter: "blur(5px)",
					boxShadow: "0 18px 48px rgba(0,0,0,0.14)",
					transform: `translateY(${moveY}px)`,
				}}
			>
				{bullets.map((item, index) => {
					const itemPop = interpolate(frame, [index * 9, index * 9 + 14], [0.8, 1], {
						extrapolateLeft: "clamp",
						extrapolateRight: "clamp",
					});
					const itemOpacity = interpolate(frame, [index * 9, index * 9 + 12], [0, 1], {
						extrapolateLeft: "clamp",
						extrapolateRight: "clamp",
					});
					return (
						<div
							key={item}
							style={{
								display: "flex",
								alignItems: "flex-start",
								gap: 16,
								marginTop: index === 0 ? 0 : 18,
								transform: `scale(${itemPop})`,
								opacity: itemOpacity,
							}}
						>
							<div
								style={{
									width: 20,
									height: 20,
									marginTop: 13,
									borderRadius: 999,
									background: "#ef4444",
									flexShrink: 0,
								}}
							/>
							<div style={{ fontSize: 40, lineHeight: 1.34, color: "#111827", fontWeight: 700 }}>
								{item}
							</div>
						</div>
					);
				})}
			</div>
		</AbsoluteFill>
	);
};

const EndingScene: React.FC = () => {
	const frame = useCurrentFrame();
	const { fps } = useVideoConfig();
	const opacity = sceneOpacity(frame, fps, 60);
	const moveY = sceneEnter(frame, fps);

	const pulse = interpolate(frame % 30, [0, 15, 29], [1, 1.06, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});

	return (
		<AbsoluteFill
			style={{
				background: "linear-gradient(160deg, #111827 0%, #1f2937 60%, #374151 100%)",
				opacity,
			}}
		>
			<div
				style={{
					position: "absolute",
					left: 76,
					right: 76,
					top: 540,
					textAlign: "center",
					transform: `translateY(${moveY}px)`,
				}}
			>
				<div style={{ color: "#f9fafb", fontSize: 72, fontWeight: 900, lineHeight: 1.2 }}>
					想要完整版路线图
				</div>
				<div style={{ color: "#d1d5db", fontSize: 44, marginTop: 20, fontWeight: 600 }}>
					评论区打「车展」我发你
				</div>
				<div
					style={{
						display: "inline-block",
						marginTop: 42,
						padding: "20px 36px",
						borderRadius: 999,
						background: "#ef4444",
						color: "white",
						fontSize: 44,
						fontWeight: 900,
						transform: `scale(${pulse})`,
					}}
				>
					关注 + 收藏 不迷路
				</div>
			</div>
		</AbsoluteFill>
	);
};

export const XhsAutoShowVideo: React.FC = () => {
	const { fps } = useVideoConfig();

	return (
		<AbsoluteFill style={{ backgroundColor: "#0f172a" }}>
			<Sequence durationInFrames={3 * fps} premountFor={1 * fps}>
				<CoverScene />
			</Sequence>

			<Sequence from={3 * fps} durationInFrames={4.5 * fps} premountFor={1 * fps}>
				<InfoCard
					title="先冲热门展台"
					subtitle="01 时间点"
					theme={["#fff4e6", "#ffe4c7"]}
					sceneFrames={4.5 * fps}
					bullets={[
						"10:00 前到场，避开第一波排队高峰",
						"先拍主展车，下午再补细节特写",
						"带一块小反光板，肤色和车漆都更好看",
					]}
				/>
			</Sequence>

			<Sequence from={7.5 * fps} durationInFrames={4.5 * fps} premountFor={1 * fps}>
				<InfoCard
					title="高赞机位公式"
					subtitle="02 出片位"
					theme={["#e8f0ff", "#d8e6ff"]}
					sceneFrames={4.5 * fps}
					bullets={[
						"45°车头 + 低机位，线条最有冲击力",
						"人车同框时，人物站车灯外侧三分位",
						"连拍 5 张，优先选“迈步瞬间”的动态感",
					]}
				/>
			</Sequence>

			<Sequence from={12 * fps} durationInFrames={4 * fps} premountFor={1 * fps}>
				<InfoCard
					title="避坑提醒"
					subtitle="03 实战经验"
					theme={["#edfcef", "#d9f8df"]}
					sceneFrames={4 * fps}
					bullets={[
						"别在强逆光位硬拍，容易人黑车亮",
						"展馆白平衡不稳，后期统一偏暖更高级",
						"文案首句带数字：点击率通常更高",
					]}
				/>
			</Sequence>

			<Sequence from={16 * fps} durationInFrames={2 * fps} premountFor={1 * fps}>
				<EndingScene />
			</Sequence>
		</AbsoluteFill>
	);
};
