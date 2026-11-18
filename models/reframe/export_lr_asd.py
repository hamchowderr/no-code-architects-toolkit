import sys, torch, numpy as np
sys.path.insert(0,'/tmp/ft/lrasd')
from model.Model import ASD_Model
from loss import lossAV
class Net(torch.nn.Module):
    def __init__(s):
        super().__init__(); s.m=ASD_Model(); s.h=lossAV()
    def forward(s, audio, video):
        o=s.m.forward_audio_visual_backend(s.m.forward_audio_frontend(audio), s.m.forward_visual_frontend(video))
        return s.h.FC(o)[:,1]
n=Net()
for e in (n.m.audioEncoder,):
    e.pool1=torch.nn.MaxPool2d((1,3),(1,2),(0,1)); e.pool2=torch.nn.MaxPool2d((1,3),(1,2),(0,1))
st=torch.load('/tmp/ft/lrasd/weight/pretrain_AVA.model',map_location='cpu')
n.m.load_state_dict({k.replace('model.','',1):v for k,v in st.items() if k.startswith('model.')})
n.h.load_state_dict({k.replace('lossAV.','',1):v for k,v in st.items() if k.startswith('lossAV.')})
n.eval()
A=torch.randn(1,100,13); V=torch.rand(1,25,112,112)*255
torch.onnx.export(n,(A,V),'lr_asd.onnx',input_names=['audio','video'],output_names=['score'],dynamic_axes={'audio':{1:'ta'},'video':{1:'tv'},'score':{0:'tv'}},opset_version=17,dynamo=False)
import onnxruntime as ort
r=Net(); r.load_state_dict(n.state_dict()); r.eval()
s=ort.InferenceSession('lr_asd.onnx')
for t in (25,50,100,7):
    A=torch.randn(1,t*4,13); V=torch.rand(1,t,112,112)*255
    with torch.no_grad(): ref=r(A,V).numpy()
    out=s.run(None,{'audio':A.numpy(),'video':V.numpy()})[0]
    print(t, out.shape, float(np.abs(out-ref).max()))
