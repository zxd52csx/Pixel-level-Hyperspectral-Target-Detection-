from torch.utils.data import DataLoader
from dataset.Implicit_dataset import HTD_dataset
from model.ICLM import * 
from model.evaluation import *
torch.set_default_tensor_type(torch.cuda.FloatTensor)
import tqdm
import time
import argparse


parser = argparse.ArgumentParser("ICLTD")
parser.add_argument('--cross_scene', action='store_true', help='Use cross-scene detection')
parser.add_argument('--model', choices=['fc', 'trans'], default='fc', help='model to use')
parser.add_argument('--epoch', type=int, default=500, help='training epoch number')
parser.add_argument('--ICL_weight', type=float, default=1, help='weigth of implicit contrastive learning')
parser.add_argument('--LSSC_weight', type=float, default=0, help='weigth of local spectral similarity constraint')
parser.add_argument('--LSSC_t', type=float, default=0.1, help='threshold for LSSC collecting candidate')
parser.add_argument('--eo', action='store_true', help='evaluation other comparison')
parser.add_argument('--weight_decay', type=float, default=5e-4, help='weight decay')
args = parser.parse_args()
print(args)

# 超参数设置
implicit_weight = args.ICL_weight
Tcan_threshold = args.LSSC_t

# 网络结构参数
LSSC_weight = args.LSSC_weight
hidden_depth = 50
Block_num = 4
Duplication = [implicit_weight for _ in range(Block_num)]

# 训练参数
lr = 0.0001
Training_epoch = args.epoch

# detection type: same scene or cross_scene
cross_scene = args.cross_scene
if cross_scene:
    data_root = 'data/MT-ABU-dataset'
    # Inscene HSI
    img_list = ['A1', 'A2', 'B1', 'B2', 'U1', 'U2']
    # HSI to generate prior spectra
    img_refer_list = ['A2', 'A1', 'B2', 'B1', 'U2', 'U1']
else:
    data_root = 'data/ABU-dataset'
    # Inscene HSI
    img_list = ['airport1', 'airport2', 'airport3', 'airport4']


# training begin
for img_id in range(len(img_list)):
    # dataset
    img_name = img_list[img_id]
    if cross_scene:
        transform = 'MT'
        img_refer = [img_refer_list[img_id]]
    else:
        transform = 'part'
        img_refer = [img_list[img_id]]
    

    # dataset
    train_dataset = HTD_dataset(data_root, img_name=img_name, img_refer=img_refer, prior_transform=[transform], divide=1, eo=args.eo)
    train_dataloader = DataLoader(train_dataset, batch_size=1, shuffle=True, generator=torch.Generator(device = 'cuda'))
    row, col = train_dataset.groundtruth.shape

    # initialization
    band_num = train_dataset.prior.shape[0]

    # model build choose from 
    # fully connected or self-attention -based detector
    if args.model == 'fc':
        net_encoder = FCbDT(band_num, hidden_depth, Duplication, train_dataset.groundtruth).cuda()
    else:
        net_encoder = STbDT(band_num, hidden_depth, Duplication, train_dataset.groundtruth).cuda()
    net_encoder.train()
    op_1 = optim.Adam(net_encoder.encoder.parameters(), lr=lr, weight_decay=args.weight_decay)
    # model training
    pbar = tqdm.tqdm(range(Training_epoch), ncols=100)
    for _, e in enumerate(pbar):
        # date prepare
        Inscene_HSI, prior = next(iter(train_dataloader))
        Inscene_HSI = Inscene_HSI[0]
        input = [prior, Inscene_HSI]
        # Generate target candidate mask
        net_encoder.eval()
        with torch.no_grad():
            Candidate_scores = net_encoder.detect(Inscene_HSI)
        Tc_mask = Candidate_scores > Tcan_threshold
        Tc_show = Tc_mask.detach().cpu().numpy().reshape(-1, row).astype(np.uint8)
        net_encoder.train()

        # optimazation iteration begin
        Detection_result, LSSC_loss = net_encoder(input, Tc_mask)
        loss = net_encoder.loss(prior.shape[0], Detection_result)
        losses = loss  + LSSC_weight * LSSC_loss
        op_1.zero_grad()
        losses.backward()
        op_1.step()

        if e % 10 == 0:
            pbar.set_description(f"Epoch {e}/{Training_epoch}")
            pbar.set_postfix({"loss:":losses.item(), "score:":(-1*loss).exp().item()})

    # model testing
    net_encoder.eval()
    t1 = time.time()
    test_data = torch.Tensor(train_dataset.test_img).cuda()
    Detection_result = net_encoder.detect(test_data.reshape(-1, band_num)).detach().cpu().numpy().reshape(row, col)

    print('ICLTD inference time comsumption:{}.'.format(time.time()-t1))
    Detection_result = Detection_result.reshape(-1, order='F')
    print('*--------- {} -----------*'.format(img_name))
    evaluate_result = [Detection_result]
    evaluate_name = ['ICLTD']
    if args.eo:
        evaluate_result = evaluate_result + train_dataset.classic_results[0]
        evaluate_name = evaluate_name + train_dataset.classic_results[1]
    AUC_list = ROC(train_dataset.groundtruth.reshape(-1, order='F'),
        evaluate_result, 
        evaluate_name,
        img_name, row, col, args.eo)
    
                    