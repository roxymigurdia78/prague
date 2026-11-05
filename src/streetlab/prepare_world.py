"""One entry point: GLB -> open sky -> residual cleanup -> non-emissive dark scene."""
import argparse
from pathlib import Path

from streetlab import sky_cleanup
from streetlab import sky_cleanup_polish


def main(argv=None):
    parser=argparse.ArgumentParser(description='GLBの蓋を除き、照明をゼロから置ける状態にします。')
    parser.add_argument('input',nargs='?',type=Path,help='元の見た目付きGLB')
    parser.add_argument('--cleaned-dir',type=Path,help='蓋の除去済みフォルダーを使う（APIなし）')
    parser.add_argument('--source-up',choices=('marble','gltf'),default='marble')
    parser.add_argument('--model',default=sky_cleanup.DEFAULT_MODEL)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--continue-dir',type=Path,help='最初の空除去処理を再開')
    args=parser.parse_args(argv)
    if args.cleaned_dir:
        if args.input or args.continue_dir:parser.error('--cleaned-dirは単独で使ってください。')
        output=args.cleaned_dir.resolve()
        if not (output/'sky_review.blend').is_file():parser.error('除去済みのsky_review.blendがありません。')
        sky_cleanup_polish.save_clean_scene(output)
    else:
        if not args.input:parser.error('GLBを指定してください。')
        options=[str(args.input),'--quality','thorough','--source-up',args.source_up,'--model',args.model]
        if args.env_file:options+=['--env-file',str(args.env_file)]
        if args.continue_dir:options+=['--continue-dir',str(args.continue_dir)]
        cleaned=sky_cleanup.main(options)
        # Keep the chosen residual-analysis directory so a failed API call resumes
        # cached groups instead of starting another analysis when this step is retried.
        import json
        from datetime import datetime
        state=cleaned/'preparation.json'
        if state.exists():
            polished=Path(json.loads(state.read_text())['polished'])
        else:
            polished=cleaned.parent/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_polished')
            state.write_text(json.dumps({'polished':str(polished)},indent=2),encoding='utf-8')
        options=[str(cleaned),'--continue-dir',str(polished),'--model',args.model]
        if args.env_file:options+=['--env-file',str(args.env_file)]
        output=sky_cleanup_polish.main(options)
    print('完了。照明を置くファイル: '+str(output/'street_relight.blend'),flush=True)
    print('GLB: '+str(output/'street_relight.glb'),flush=True)
    return output


if __name__=='__main__':main()
